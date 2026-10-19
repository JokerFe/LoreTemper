import { useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  Check,
  ChevronRight,
  CircleHelp,
  Clock3,
  Cloud,
  Cpu,
  Film,
  FolderOpen,
  FolderOutput,
  HardDrive,
  LoaderCircle,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Sparkles,
  X,
} from "lucide-react";

type Provider = "codex" | "ollama";
type Estimate = {
  total_tokens: number | null;
  usage: {
    input_tokens: number;
    cached_input_tokens: number;
    output_tokens: number;
  } | null;
  api_equivalent_usd: number | null;
  multiplier: number | null;
  caveat: string;
  pricing_checked?: string;
  pricing_source?: string;
};
type Model = {
  model: string;
  displayName: string;
  description?: string;
  inputModalities?: string[];
  disabled?: boolean;
  error?: string;
  isDefault?: boolean;
  defaultReasoningEffort?: string;
  supportedReasoningEfforts?: {
    reasoningEffort: string;
    description: string;
  }[];
  serviceTiers?: { id: string; name: string; description: string }[];
  cost_multiplier?: number | null;
  hour_estimate?: Estimate;
};
type Catalog = {
  models: Model[];
  logged_in: boolean;
  source: string;
  fetched_at: number;
};
type Form = {
  provider: Provider;
  model: string;
  reasoning: string;
  service_tier: string;
  vision_model: string;
  text_model: string;
  output_directory: string;
};
type Video = {
  path: string;
  name: string;
  duration_ms: number;
  size_bytes: number;
};
type Preview = {
  files: Video[];
  errors: { path: string; error: string }[];
  duration_ms: number;
  output_directory: string;
  estimate: Estimate;
};
type Item = {
  id: string;
  path: string;
  status: string;
  run_id?: string;
  run_status?: string;
  stage?: string;
  progress_done?: number;
  progress_total?: number;
  error?: string;
  run_error?: string;
};
type Batch = {
  id: string;
  created_at: number;
  provider: Provider;
  model: string;
  output_directory: string;
  items: Item[];
};
type Receipt = {
  provider: Provider;
  usage?: {
    input_tokens?: number;
    cached_input_tokens?: number;
    output_tokens?: number;
  };
  api_equivalent_usd: number | null;
  unmetered_attempts?: number;
};

async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch("/api/v1" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-VT-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok)
    throw new Error(
      typeof value.message === "string"
        ? value.message
        : typeof value.detail === "string"
          ? value.detail
          : "请检查输入后重试",
    );
  return value;
}
const duration = (ms: number) =>
  `${Math.floor(ms / 3600000) ? Math.floor(ms / 3600000) + " 小时 " : ""}${Math.round((ms % 3600000) / 60000)} 分钟`;
const tokens = (n: number | null | undefined) =>
  n == null ? "待估算" : `${(n / 10000).toFixed(1)} 万`;
const multiplier = (n: number | null | undefined) =>
  n == null ? "待估算" : `${Number(n.toFixed(2))}×`;
const names: Record<string, string> = {
  queued: "排队中",
  imported: "等待分析",
  running: "正在学习",
  succeeded: "已生成",
  succeeded_with_warnings: "已生成 · 待审阅",
  failed: "失败",
  paused: "已暂停",
  pause_requested: "正在暂停",
  cancel_requested: "正在取消",
  cancelled: "已取消",
};
const stageNames: Record<string, string> = {
  probe: "检查视频",
  extract_audio: "提取音频",
  transcribe: "转写讲解",
  select_frames: "筛选画面",
  thematic_study: "按主题取证",
  summarize: "整理内容总结",
  analyze_frames: "分析画面",
  fuse: "整理知识",
  export: "导出学习资料",
  complete: "已完成",
};
const initial: Form = {
  provider: "codex",
  model: "",
  reasoning: "medium",
  service_tier: "default",
  vision_model: "",
  text_model: "",
  output_directory: "",
};

export function AnalysisPage({
  notify,
  fail,
  onLibrary,
}: {
  notify: (text: string) => void;
  fail: (text: string) => void;
  onLibrary: () => void;
}) {
  const [form, setForm] = useState<Form>(initial);
  const [paths, setPaths] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [modelError, setModelError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshKey, setRefreshKey] = useState(0);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewing, setPreviewing] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [picking, setPicking] = useState(false);
  const [batches, setBatches] = useState<Batch[]>([]);
  const [receipts, setReceipts] = useState<Record<string, Receipt>>({});
  const [busyRun, setBusyRun] = useState("");
  const requestId = useRef(crypto.randomUUID());
  const [defaultsReady, setDefaultsReady] = useState(false);
  const effectivePaths = [
    ...new Set([
      ...paths,
      ...input
        .split("\n")
        .map((x) => x.trim())
        .filter(Boolean),
    ]),
  ];
  const pathsKey = JSON.stringify(effectivePaths);
  const selectedModel = catalog?.models.find((m) => m.model === form.model);
  const imageModels =
    catalog?.models.filter(
      (m) =>
        !m.disabled &&
        (m.inputModalities || ["text", "image"]).includes("image"),
    ) || [];
  const textModels =
    catalog?.models.filter(
      (m) => !m.disabled && (m.inputModalities || ["text"]).includes("text"),
    ) || [];

  useEffect(() => {
    let alive = true;
    request<Form>("/analysis/defaults")
      .then((v) => {
        if (alive) {
          setForm(v);
          setDefaultsReady(true);
        }
      })
      .catch((e) => {
        if (alive) {
          fail(e.message);
          setDefaultsReady(true);
        }
      });
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => {
    if (!defaultsReady) return;
    let alive = true;
    setLoading(true);
    setCatalog(null);
    setModelError("");
    request<Catalog>(`/analysis/models?provider=${form.provider}`)
      .then((value) => {
        if (!alive) return;
        setCatalog(value);
        setForm((previous) => {
          const images = value.models.filter(
            (m) =>
              !m.disabled &&
              (m.inputModalities || ["text", "image"]).includes("image"),
          );
          const texts = value.models.filter(
            (m) =>
              !m.disabled && (m.inputModalities || ["text"]).includes("text"),
          );
          const current =
            images.find((m) => m.model === previous.model) ||
            images.find((m) => m.isDefault) ||
            images[0];
          const effort = current?.supportedReasoningEfforts?.some(
            (e) => e.reasoningEffort === previous.reasoning,
          )
            ? previous.reasoning
            : current?.defaultReasoningEffort || "medium";
          return {
            ...previous,
            model:
              previous.provider === "ollama"
                ? previous.model
                : current?.model || "",
            reasoning: effort,
            service_tier: "default",
            vision_model:
              previous.provider === "codex"
                ? previous.vision_model
                : images.some((m) => m.model === previous.vision_model)
                  ? previous.vision_model
                  : images[0]?.model || "",
            text_model:
              previous.provider === "codex"
                ? previous.text_model
                : texts.some((m) => m.model === previous.text_model)
                  ? previous.text_model
                  : texts[0]?.model || "",
          };
        });
      })
      .catch((e) => {
        if (alive) setModelError(e.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [form.provider, refreshKey, defaultsReady]);
  useEffect(() => {
    requestId.current = crypto.randomUUID();
    setPreview(null);
    setPreviewError("");
    if (!effectivePaths.length || !form.output_directory.trim()) {
      setPreviewing(false);
      return;
    }
    let alive = true;
    setPreviewing(true);
    const timer = setTimeout(() => {
      request<Preview>("/analysis/preview", "POST", {
        ...form,
        paths: JSON.parse(pathsKey),
        model: form.model || "unknown",
      })
        .then((v) => {
          if (alive) setPreview(v);
        })
        .catch((e) => {
          if (alive) setPreviewError(e.message);
        })
        .finally(() => {
          if (alive) setPreviewing(false);
        });
    }, 550);
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [
    pathsKey,
    form.output_directory,
    form.provider,
    form.model,
    form.reasoning,
    form.service_tier,
    form.vision_model,
    form.text_model,
  ]);
  useEffect(() => {
    let alive = true;
    const refresh = async () => {
      try {
        const next = await request<Batch[]>("/analysis/jobs");
        if (!alive) return;
        setBatches(next);
        const ids = next
          .flatMap((b) => b.items)
          .map((i) => i.run_id)
          .filter((id): id is string => Boolean(id));
        const values = await Promise.all(
          ids
            .slice(0, 60)
            .map(
              async (id) =>
                [id, await request<Receipt>(`/runs/${id}/usage`)] as const,
            ),
        );
        if (alive) setReceipts(Object.fromEntries(values));
      } catch (e) {
        if (alive) console.warn("任务记录暂时不可用", e);
      }
    };
    refresh();
    const timer = setInterval(refresh, 3500);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  function field<K extends keyof Form>(key: K, value: Form[K]) {
    setForm((previous) => ({ ...previous, [key]: value }));
  }
  async function pick(kind: "files" | "folder" | "output") {
    setPicking(true);
    try {
      const value = await request<{ paths: string[] }>(
        "/analysis/pick",
        "POST",
        { kind },
      );
      if (value.paths.length) {
        if (kind === "output") field("output_directory", value.paths[0]);
        else
          setPaths((previous) => [...new Set([...previous, ...value.paths])]);
      }
    } catch (e) {
      fail(e instanceof Error ? e.message : "选择失败");
    } finally {
      setPicking(false);
    }
  }
  async function submit() {
    if (!preview || preview.errors.length || submitting) return;
    setSubmitting(true);
    try {
      await request("/analysis/jobs", "POST", {
        ...form,
        paths: effectivePaths,
        request_id: requestId.current,
      });
      notify(`已将 ${preview.files.length} 个视频加入学习队列`);
      setPaths([]);
      setInput("");
      setPreview(null);
      setBatches(await request<Batch[]>("/analysis/jobs"));
    } catch (e) {
      fail(e instanceof Error ? e.message : "创建任务失败");
    } finally {
      setSubmitting(false);
    }
  }
  async function control(item: Item, action: string) {
    setBusyRun(item.id);
    try {
      await request(`/runs/${item.run_id}/${action}`, "POST", {});
      setBatches(await request<Batch[]>("/analysis/jobs"));
    } catch (e) {
      fail(e instanceof Error ? e.message : "任务操作失败");
    } finally {
      setBusyRun("");
    }
  }
  const estimate = preview?.estimate || selectedModel?.hour_estimate;
  const rate =
    form.provider === "ollama"
      ? 0
      : estimate?.multiplier == null
        ? null
        : estimate.multiplier *
          (preview ? 1 : form.service_tier === "priority" ? 2 : 1);
  const usd =
    form.provider === "ollama"
      ? 0
      : estimate?.api_equivalent_usd == null
        ? null
        : estimate.api_equivalent_usd *
          (preview ? 1 : form.service_tier === "priority" ? 2 : 1);
  const available =
    !loading &&
    !modelError &&
    catalog?.logged_in &&
    (form.provider === "codex"
      ? !!selectedModel
      : !!form.vision_model && !!form.text_model);
  const canSubmit =
    available &&
    !!preview?.files.length &&
    !preview.errors.length &&
    !previewing &&
    !submitting;
  return (
    <main className="analysis-page">
      <div className="analysis-heading">
        <div>
          <div className="eyebrow">YOUR NEXT LEARNING SESSION</div>
          <h1>把好内容，变成自己的知识。</h1>
          <p>选好视频和模型，剩下的交给学习引擎。</p>
        </div>
        <span className="analysis-local">
          <HardDrive size={14} /> 本机工作台
        </span>
      </div>
      <div className="analysis-layout">
        <div className="analysis-form">
          <section className="analysis-section">
            <div className="section-title">
              <span>01</span>
              <h2>选择分析引擎</h2>
            </div>
            <div className="engine-options">
              <button
                className={
                  "engine-option " +
                  (form.provider === "codex" ? "selected" : "")
                }
                onClick={() => field("provider", "codex")}
                aria-pressed={form.provider === "codex"}
              >
                <Cloud size={23} />
                <div>
                  <b>Codex 订阅</b>
                  <small>使用本机客户端登录的 ChatGPT 账户</small>
                </div>
                <span className="engine-check">
                  {form.provider === "codex" && <Check size={14} />}
                </span>
              </button>
              <button
                className={
                  "engine-option " +
                  (form.provider === "ollama" ? "selected" : "")
                }
                onClick={() => field("provider", "ollama")}
                aria-pressed={form.provider === "ollama"}
              >
                <Cpu size={23} />
                <div>
                  <b>Ollama 本地模型</b>
                  <small>在这台电脑运行，无模型 API 费用</small>
                </div>
                <span className="engine-check">
                  {form.provider === "ollama" && <Check size={14} />}
                </span>
              </button>
            </div>
            <div className="catalog-line">
              <span className={modelError ? "catalog-error" : "muted"}>
                {loading
                  ? "正在读取本机模型…"
                  : modelError ||
                    `${catalog?.source || ""} · ${catalog?.models.length || 0} 个模型`}
              </span>
              <button
                onClick={() => setRefreshKey((k) => k + 1)}
                disabled={loading}
                aria-label="刷新模型"
              >
                <RefreshCw size={13} className={loading ? "spin" : ""} />
                刷新模型
              </button>
            </div>
            {catalog && !catalog.logged_in && (
              <div className="analysis-warning">
                请先在 Codex 客户端使用 ChatGPT 账户登录，再刷新模型。这里不使用
                API 密钥。
              </div>
            )}
            {!loading && catalog && imageModels.length === 0 && (
              <div className="analysis-warning">
                没有可用于图片分析的模型。请安装视觉模型或检查 Codex 账户。
              </div>
            )}
            {form.provider === "codex" ? (
              <>
                <label className="analysis-label">
                  分析模型
                  <select
                    aria-label="分析模型"
                    value={form.model}
                    disabled={loading || !imageModels.length}
                    onChange={(e) => {
                      const model = imageModels.find(
                        (m) => m.model === e.target.value,
                      );
                      setForm((f) => ({
                        ...f,
                        model: e.target.value,
                        reasoning: model?.supportedReasoningEfforts?.some(
                          (x) => x.reasoningEffort === f.reasoning,
                        )
                          ? f.reasoning
                          : model?.defaultReasoningEffort || "medium",
                        service_tier: "default",
                      }));
                    }}
                  >
                    <option value="" disabled>
                      选择当前可用模型
                    </option>
                    {imageModels.map((m) => (
                      <option value={m.model} key={m.model}>
                        {m.displayName} · {multiplier(m.cost_multiplier)}
                      </option>
                    ))}
                  </select>
                </label>
                <div className="analysis-two">
                  <label className="analysis-label">
                    思考强度
                    <select
                      aria-label="思考强度"
                      value={form.reasoning}
                      disabled={loading || !selectedModel}
                      onChange={(e) => field("reasoning", e.target.value)}
                    >
                      {selectedModel?.supportedReasoningEfforts?.map((e) => (
                        <option
                          key={e.reasoningEffort}
                          value={e.reasoningEffort}
                        >
                          {e.reasoningEffort}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="analysis-label">
                    处理速度
                    <select
                      aria-label="处理速度"
                      value={form.service_tier}
                      onChange={(e) => field("service_tier", e.target.value)}
                    >
                      <option value="default">标准速度</option>
                      {selectedModel?.serviceTiers
                        ?.filter((t) => t.id === "priority")
                        .map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.name} · 费用约 2×
                          </option>
                        ))}
                    </select>
                  </label>
                </div>
                <p className="analysis-caption">
                  完整讲解建立主题 → 原图取证 → 查漏补图 →
                  整课内容总结。转写和抽帧在本机完成，讲解与所需画面由 Codex
                  在线分析。
                </p>
              </>
            ) : (
              <>
                <div className="analysis-two">
                  <label className="analysis-label">
                    视觉模型
                    <select
                      aria-label="视觉模型"
                      value={form.vision_model}
                      disabled={loading || !imageModels.length}
                      onChange={(e) => field("vision_model", e.target.value)}
                    >
                      <option value="" disabled>
                        选择视觉模型
                      </option>
                      {imageModels.map((m) => (
                        <option key={m.model}>{m.model}</option>
                      ))}
                    </select>
                  </label>
                  <label className="analysis-label">
                    文本模型
                    <select
                      aria-label="文本模型"
                      value={form.text_model}
                      disabled={loading || !textModels.length}
                      onChange={(e) => field("text_model", e.target.value)}
                    >
                      <option value="" disabled>
                        选择文本模型
                      </option>
                      {textModels.map((m) => (
                        <option key={m.model}>{m.model}</option>
                      ))}
                    </select>
                  </label>
                </div>
                <p className="analysis-caption">
                  列出本机已安装模型；视觉模型需要支持图片输入。语音、画面与笔记均在本机处理。
                </p>
              </>
            )}
          </section>
          <section className="analysis-section">
            <div className="section-title">
              <span>02</span>
              <h2>添加学习视频</h2>
              <small>单个、多个，或整个文件夹</small>
            </div>
            <div className="source-picker">
              <div className="source-icon">
                <Film size={27} />
              </div>
              <h3>从你的电脑添加视频</h3>
              <p>视频保留在原位置，不需要上传整段文件。</p>
              <div>
                <button
                  className="secondary"
                  disabled={picking}
                  onClick={() => pick("files")}
                >
                  <Plus size={16} />
                  选择视频文件
                </button>
                <button
                  className="secondary"
                  disabled={picking}
                  onClick={() => pick("folder")}
                >
                  <FolderOpen size={16} />
                  选择视频文件夹
                </button>
              </div>
              {picking && <small>请在系统选择窗口中操作…</small>}
            </div>
            <label className="analysis-label manual-path">
              也可以直接输入文件或文件夹路径
              <textarea
                aria-label="输入视频路径"
                placeholder={
                  "/Users/你的名字/Movies/课程\n或填写单个视频的完整路径，每行一个"
                }
                rows={2}
                value={input}
                onChange={(e) => setInput(e.target.value)}
              />
            </label>
            {paths.length > 0 && (
              <div className="chosen-paths">
                {paths.map((path) => (
                  <div key={path}>
                    <FolderOpen size={14} />
                    <span title={path}>{path}</span>
                    <button
                      aria-label={"移除 " + path}
                      onClick={() =>
                        setPaths((p) => p.filter((x) => x !== path))
                      }
                    >
                      <X size={14} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            {previewing && (
              <p className="analysis-caption">
                <LoaderCircle size={13} className="spin" /> 正在检查视频与时长…
              </p>
            )}
            {previewError && (
              <div className="analysis-warning">{previewError}</div>
            )}
            {preview?.errors.map((e) => (
              <div className="analysis-warning" key={e.path}>
                {e.path.split("/").pop()}：{e.error}
              </div>
            ))}
            {!!preview?.files.length && (
              <div className="video-selection">
                <header>
                  <b>已识别 {preview.files.length} 个视频</b>
                  <span>共 {duration(preview.duration_ms)}</span>
                </header>
                <div>
                  {preview.files.map((f) => (
                    <article key={f.path}>
                      <Film size={16} />
                      <span title={f.path}>{f.name}</span>
                      <small>{duration(f.duration_ms)}</small>
                    </article>
                  ))}
                </div>
              </div>
            )}
            <p className="analysis-caption">
              支持 MP4、MOV、MKV
              等常见视频格式。文件夹包含子目录，重复路径自动合并。
            </p>
          </section>
          <section className="analysis-section">
            <div className="section-title">
              <span>03</span>
              <h2>保存到哪里</h2>
            </div>
            <label className="analysis-label">
              输出文件夹
              <div className="output-input">
                <input
                  aria-label="输出文件夹"
                  value={form.output_directory}
                  onChange={(e) => field("output_directory", e.target.value)}
                  placeholder="填写输出文件夹的绝对路径"
                />
                <button
                  className="secondary"
                  disabled={picking}
                  onClick={() => pick("output")}
                >
                  <FolderOutput size={15} />
                  浏览
                </button>
              </div>
            </label>
            <p className="analysis-caption">
              每个视频独立保存内容总结、原始字幕、来源截图和用量记录。重复分析会创建新版本。
            </p>
          </section>
        </div>
        <aside className="analysis-aside">
          <div className="estimate-card">
            <div className="estimate-title">
              <Sparkles size={18} />
              <h2>学习消耗预估</h2>
              <span>ESTIMATE</span>
            </div>
            <div className="estimate-multiplier">
              <strong>{multiplier(rate)}</strong>
              <span>
                {form.provider === "ollama"
                  ? "云端模型费用"
                  : "API 等值费用倍率"}
              </span>
            </div>
            <p className="estimate-baseline">GPT-6.1 Sol · 标准速度 = 1×</p>
            <div className="estimate-rule" />
            <div className="estimate-row">
              <span>视频时长</span>
              <b>
                {preview ? duration(preview.duration_ms) : "以 60 分钟为例"}
              </b>
            </div>
            <div className="estimate-row">
              <span>预计总 token</span>
              <b>
                {form.provider === "ollama"
                  ? "本地用量未估算"
                  : tokens(estimate?.total_tokens)}
              </b>
            </div>
            <div className="estimate-row">
              <span>输入 / 输出</span>
              <b>
                {form.provider === "ollama"
                  ? "—"
                  : `${tokens(estimate?.usage?.input_tokens)} / ${tokens(estimate?.usage?.output_tokens)}`}
              </b>
            </div>
            <div className="estimate-row">
              <span>其中缓存输入</span>
              <b>
                {form.provider === "ollama"
                  ? "—"
                  : tokens(estimate?.usage?.cached_input_tokens)}
              </b>
            </div>
            <div className="estimate-total">
              <span>
                {form.provider === "ollama" ? "模型 API 费用" : "预计 API 等值"}
              </span>
              <b>{usd == null ? "待估算" : `$${usd.toFixed(2)}`}</b>
            </div>
            <div className="estimate-note">
              <CircleHelp size={15} />
              <p>
                {form.provider === "codex"
                  ? "使用已登录的订阅账户。金额与倍率仅用于比较，不代表额外扣费或订阅额度比例。其他模型与思考档位的 token 变化尚未实测。"
                  : "Ollama 全程在本机运行。0× 仅表示无模型 API 费用，电费与设备成本另计。"}
              </p>
            </div>
            {form.provider === "codex" && (
              <p className="estimate-source">
                基于 7 门课程、855 分钟调用记录。
                <br />
                价格核对于 2026-10-19 ·{" "}
                <a
                  href="https://developers.openai.com/api/docs/pricing"
                  target="_blank"
                  rel="noreferrer"
                >
                  定价依据 <ArrowUpRight size={11} />
                </a>
              </p>
            )}
            <button
              className="primary start-learning"
              disabled={!canSubmit}
              onClick={submit}
            >
              {submitting ? (
                <LoaderCircle size={17} className="spin" />
              ) : (
                <Play size={16} />
              )}
              {submitting ? "正在加入队列…" : "开始学习"}
            </button>
            <p className="start-hint">
              {!effectivePaths.length
                ? "添加视频后，可查看本次预估"
                : !form.output_directory.trim()
                  ? "请指定输出文件夹"
                  : previewing
                    ? "正在核对所选视频"
                    : !available
                      ? "请先选择可用的模型与登录账户"
                      : "加入队列后按顺序处理，可随时暂停"}
            </p>
          </div>
          <div className="learning-promise">
            <Check size={17} />
            <div>
              <b>保留知识，也保留出处</b>
              <p>
                重点、方法与案例进入内容总结，截图和完整讲解留作查证。生成后仍需来源审阅。
              </p>
            </div>
          </div>
        </aside>
      </div>
      <section className="analysis-jobs">
        <div className="section-title">
          <Clock3 size={19} />
          <h2>最近的学习任务</h2>
          <button onClick={onLibrary}>
            前往视频资料库
            <ChevronRight size={14} />
          </button>
        </div>
        {!batches.length ? (
          <div className="jobs-empty">
            还没有从这里创建学习任务。选好视频，开始你的第一段学习。
          </div>
        ) : (
          batches.map((batch) => (
            <div className="batch-card" key={batch.id}>
              <header>
                <div>
                  <span className="provider-pill">
                    {batch.provider === "codex" ? (
                      <Cloud size={12} />
                    ) : (
                      <Cpu size={12} />
                    )}
                    {batch.provider === "codex" ? "Codex" : "Ollama"}
                  </span>
                  <b>{batch.model}</b>
                  <small>
                    {new Date(batch.created_at * 1000).toLocaleString("zh-CN")}
                  </small>
                </div>
                <p title={batch.output_directory}>
                  <FolderOutput size={12} />
                  {batch.output_directory}
                </p>
              </header>
              {batch.items.map((item) => {
                const status = item.run_status || item.status;
                const receipt = item.run_id ? receipts[item.run_id] : undefined;
                return (
                  <article className="job-item" key={item.id}>
                    <Film size={17} />
                    <div className="job-description">
                      <b title={item.path}>{item.path.split("/").pop()}</b>
                      <small>
                        {item.error ||
                          item.run_error ||
                          stageNames[item.stage || ""] ||
                          "正在准备视频"}
                        {!!item.progress_total &&
                          ` · ${item.progress_done}/${item.progress_total}`}
                      </small>
                      {receipt?.usage && (
                        <small>
                          已记录{" "}
                          {tokens(
                            (receipt.usage.input_tokens || 0) +
                              (receipt.usage.output_tokens || 0),
                          )}{" "}
                          token · API 等值{" "}
                          {receipt.api_equivalent_usd == null
                            ? "待估算"
                            : `$${receipt.api_equivalent_usd.toFixed(3)}`}
                          {receipt.unmetered_attempts
                            ? " · 部分失败调用用量未知"
                            : ""}
                        </small>
                      )}
                    </div>
                    <span className={"job-status " + status}>
                      {names[status] || status}
                    </span>
                    <div className="job-actions">
                      {item.run_id &&
                        ["queued", "running"].includes(status) && (
                          <button
                            disabled={busyRun === item.id}
                            aria-label="暂停任务"
                            onClick={() => control(item, "pause")}
                          >
                            <Pause size={14} />
                          </button>
                        )}
                      {item.run_id &&
                        ["paused", "failed", "cancelled"].includes(status) && (
                          <button
                            disabled={busyRun === item.id}
                            aria-label="继续任务"
                            onClick={() => control(item, "retry")}
                          >
                            <Play size={14} />
                          </button>
                        )}
                      {item.run_id &&
                        [
                          "queued",
                          "running",
                          "paused",
                          "pause_requested",
                        ].includes(status) && (
                          <button
                            disabled={busyRun === item.id}
                            aria-label="取消任务"
                            onClick={() => control(item, "cancel")}
                          >
                            <X size={14} />
                          </button>
                        )}
                    </div>
                  </article>
                );
              })}
            </div>
          ))
        )}
      </section>
    </main>
  );
}
