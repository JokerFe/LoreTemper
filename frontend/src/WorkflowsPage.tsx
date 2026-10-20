import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  BookOpen,
  Check,
  Cloud,
  Cpu,
  FileText,
  FolderOpen,
  LoaderCircle,
  Pause,
  Play,
  RefreshCw,
  Sparkles,
  WandSparkles,
  X,
} from "lucide-react";

type Mode = "study" | "skill" | "combined";
type Stage = {
  provider: "auto" | "codex" | "ollama";
  model: string;
  reasoning: string;
  service_tier: string;
  vision_model: string;
  text_model: string;
};
type Model = {
  model: string;
  displayName: string;
  inputModalities?: string[];
  disabled?: boolean;
  cost_multiplier?: number | null;
  supportedReasoningEfforts?: { reasoningEffort: string }[];
  serviceTiers?: { id: string; name: string }[];
};
type Config = {
  mode: Mode;
  video_paths: string[];
  note_paths: string[];
  auto_input: boolean;
  output_directory: string;
  study: Stage;
  skill: Stage;
  skill_name: string;
  skill_goal: string;
};
type Estimate = {
  api_equivalent_usd?: number | null;
  total_tokens?: number | null;
  multiplier?: number | null;
  token_range?: number[];
  usd_range?: (number | null)[];
  basis?: string;
};
type Resolved = {
  output_directory: string;
  video_paths: string[];
  note_paths: string[];
  study: Stage | null;
  skill: Stage | null;
  videos: { name: string; duration_ms: number }[];
  notes: { name: string; path: string; chars: number }[];
  errors: { path: string; error: string }[];
  quality_profile?: string;
  estimates: { study?: Estimate; skill?: Estimate };
};
type Job = {
  id: string;
  mode: Mode;
  status: string;
  stage: string;
  error?: string;
  created_at: number;
  config: Resolved;
  result: {
    skill_file?: string;
    source_count?: number;
    generation_report?: {
      rule_count: number;
      capability_count: number;
      source_count: number;
      profile: string;
    } | null;
  };
  skill_usage?: {
    usage: { input_tokens?: number; output_tokens?: number };
    api_equivalent_usd?: number | null;
    unmetered_attempts?: number;
  };
  items: {
    id: string;
    path: string;
    run_id?: string;
    run_status?: string;
    stage?: string;
    run_error?: string;
  }[];
};
async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const r = await fetch("/api/v1" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-VT-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const value = await r.json();
  if (!r.ok)
    throw new Error(
      typeof value.message === "string"
        ? value.message
        : typeof value.detail === "string"
          ? value.detail
          : "请求未完成，请检查输入",
    );
  return value;
}
const automatic = (): Stage => ({
  provider: "auto",
  model: "",
  reasoning: "",
  service_tier: "default",
  vision_model: "",
  text_model: "",
});
const modeNames: Record<Mode, string> = {
  study: "视频学习",
  skill: "笔记生成 Skill",
  combined: "学习并生成 Skill",
};
const statusNames: Record<string, string> = {
  queued: "等待执行",
  learning: "视频学习中",
  running: "正在生成 Skill",
  succeeded: "已生成",
  failed: "失败",
  paused: "已暂停",
  cancelled: "已取消",
};
const count = (value?: number | null) =>
  value == null ? "待估算" : (value / 10000).toFixed(1) + " 万";
const money = (value?: number | null) =>
  value == null ? "待估算" : "$" + value.toFixed(2);
const lines = (value: string) =>
  value
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);

function ModelSettings({
  title,
  vision,
  value,
  onChange,
}: {
  title: string;
  vision: boolean;
  value: Stage;
  onChange: (value: Stage) => void;
}) {
  const [models, setModels] = useState<Model[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    if (value.provider === "auto") {
      setModels([]);
      setError("");
      return;
    }
    let alive = true;
    setLoading(true);
    setError("");
    api<{ models: Model[]; logged_in: boolean }>(
      "/analysis/models?provider=" + value.provider,
    )
      .then((v) => {
        if (alive) {
          setModels(v.models.filter((m) => !m.disabled));
          if (!v.logged_in) setError("请先在 Codex 客户端登录订阅账户");
        }
      })
      .catch((e) => {
        if (alive) setError(e.message);
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [value.provider, refresh]);
  const compatible = models.filter((m) =>
    (m.inputModalities || ["text", "image"]).includes(
      vision ? "image" : "text",
    ),
  );
  const selected = models.find((m) => m.model === value.model);
  return (
    <div className="workflow-model">
      <div className="workflow-model-title">
        <b>{title}</b>
        <small>{vision ? "讲解与画面理解" : "提炼、对照与编写"}</small>
      </div>
      <label className="analysis-label">
        运行方式
        <select
          aria-label={title + "运行方式"}
          value={value.provider}
          onChange={(e) =>
            onChange({
              ...automatic(),
              provider: e.target.value as Stage["provider"],
            })
          }
        >
          <option value="auto">自动选择可用引擎与模型</option>
          <option value="codex">Codex 订阅</option>
          <option value="ollama">Ollama 本地</option>
        </select>
      </label>
      {value.provider === "auto" ? (
        <p className="analysis-caption">
          优先使用已登录的
          Codex，无法使用时检查本地模型。配置结果会先显示，开始后固定，不会中途自动切换。
        </p>
      ) : (
        <>
          <div className="catalog-line">
            <span>
              {loading
                ? "正在读取模型…"
                : error || `${models.length} 个可用模型`}
            </span>
            <button
              aria-label={title + "刷新模型"}
              disabled={loading}
              onClick={() => setRefresh((n) => n + 1)}
            >
              <RefreshCw size={12} />
              刷新
            </button>
          </div>
          <label className="analysis-label">
            {value.provider === "ollama" && vision ? "视觉模型" : "模型"}
            <select
              aria-label={title + "模型"}
              disabled={loading}
              value={
                value.provider === "ollama" && vision
                  ? value.vision_model
                  : value.model
              }
              onChange={(e) =>
                onChange({
                  ...value,
                  model:
                    vision && value.provider === "ollama"
                      ? value.model
                      : e.target.value,
                  vision_model:
                    vision && value.provider === "ollama"
                      ? e.target.value
                      : value.vision_model,
                  reasoning: "",
                  service_tier: "default",
                })
              }
            >
              <option value="">自动选择兼容模型</option>
              {compatible.map((m) => (
                <option value={m.model} key={m.model}>
                  {m.displayName}
                  {m.cost_multiplier == null ? "" : ` · ${m.cost_multiplier}×`}
                </option>
              ))}
            </select>
          </label>
          {value.provider === "ollama" && vision && (
            <label className="analysis-label">
              文本模型
              <select
                aria-label={title + "文本模型"}
                value={value.text_model}
                onChange={(e) =>
                  onChange({ ...value, text_model: e.target.value })
                }
              >
                <option value="">自动选择文本模型</option>
                {models
                  .filter((m) =>
                    (m.inputModalities || ["text"]).includes("text"),
                  )
                  .map((m) => (
                    <option key={m.model}>{m.model}</option>
                  ))}
              </select>
            </label>
          )}
          {value.provider === "codex" && (
            <div className="analysis-two">
              <label className="analysis-label">
                思考强度
                <select
                  aria-label={title + "思考强度"}
                  value={value.reasoning}
                  onChange={(e) =>
                    onChange({ ...value, reasoning: e.target.value })
                  }
                >
                  <option value="">自动</option>
                  {selected?.supportedReasoningEfforts?.map((e) => (
                    <option key={e.reasoningEffort}>{e.reasoningEffort}</option>
                  ))}
                </select>
              </label>
              <label className="analysis-label">
                处理速度
                <select
                  value={value.service_tier}
                  onChange={(e) =>
                    onChange({ ...value, service_tier: e.target.value })
                  }
                >
                  <option value="default">标准</option>
                  {selected?.serviceTiers
                    ?.filter((t) => t.id === "priority")
                    .map((t) => (
                      <option value={t.id} key={t.id}>
                        {t.name}
                      </option>
                    ))}
                </select>
              </label>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function WorkflowsPage({
  notify,
  fail,
  onLibrary,
}: {
  notify: (text: string) => void;
  fail: (text: string) => void;
  onLibrary: () => void;
}) {
  const [config, setConfig] = useState<Config>({
    mode: "study",
    video_paths: [],
    note_paths: [],
    auto_input: true,
    output_directory: "",
    study: automatic(),
    skill: automatic(),
    skill_name: "course-design-guide",
    skill_goal: "将学习笔记提炼为帮助页面设计与开发的有来源指导",
  });
  const [videoInput, setVideoInput] = useState("");
  const [noteInput, setNoteInput] = useState("");
  const [defaults, setDefaults] = useState<{
    video_paths: string[];
    note_paths: string[];
    output_directory: string;
  } | null>(null);
  const [resolved, setResolved] = useState<Resolved | null>(null);
  const [pending, setPending] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [jobsError, setJobsError] = useState("");
  const identity = useRef(crypto.randomUUID());
  const revision = useRef(0);
  useEffect(() => {
    api<typeof defaults>("/workflows/defaults")
      .then(setDefaults)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    let alive = true;
    const refresh = () =>
      api<Job[]>("/workflows/jobs")
        .then((v) => {
          if (alive) {
            setJobs(v);
            setJobsError("");
          }
        })
        .catch((e) => {
          if (alive) setJobsError(e.message);
        });
    refresh();
    const t = setInterval(refresh, 3500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);
  function edit(patch: Partial<Config>) {
    revision.current++;
    identity.current = crypto.randomUUID();
    setResolved(null);
    setError("");
    setConfig((c) => ({ ...c, ...patch }));
  }
  async function pick(
    kind: "files" | "folder" | "notes" | "notes_folder" | "output",
  ) {
    setPicking(true);
    try {
      const v = await api<{ paths: string[] }>("/analysis/pick", "POST", {
        kind,
      });
      if (!v.paths.length) return;
      if (kind === "output") edit({ output_directory: v.paths[0] });
      else if (kind.startsWith("notes")) {
        const paths = [...new Set([...config.note_paths, ...v.paths])];
        setNoteInput(paths.join("\n"));
        edit({ note_paths: paths });
      } else {
        const paths = [...new Set([...config.video_paths, ...v.paths])];
        setVideoInput(paths.join("\n"));
        edit({ video_paths: paths });
      }
    } catch (e) {
      fail(e instanceof Error ? e.message : "选择失败");
    } finally {
      setPicking(false);
    }
  }
  async function resolve() {
    const version = revision.current;
    setPending(true);
    setError("");
    try {
      const v = await api<Resolved>("/workflows/resolve", "POST", config);
      if (version === revision.current) setResolved(v);
    } catch (e) {
      if (version === revision.current)
        setError(e instanceof Error ? e.message : "配置失败");
    } finally {
      setPending(false);
    }
  }
  async function start() {
    if (!resolved || submitting) return;
    setSubmitting(true);
    // Submit the concrete preview. Auto discovery cannot silently widen the inputs or switch models.
    const payload = {
      ...config,
      video_paths: resolved.video_paths,
      note_paths: resolved.note_paths,
      auto_input: false,
      output_directory: resolved.output_directory,
      study: resolved.study || config.study,
      skill: resolved.skill || config.skill,
      request_id: identity.current,
    };
    try {
      await api("/workflows/jobs", "POST", payload);
      notify("已创建「" + modeNames[config.mode] + "」任务");
      setJobs(await api<Job[]>("/workflows/jobs"));
      identity.current = crypto.randomUUID();
      setResolved(null);
    } catch (e) {
      fail(e instanceof Error ? e.message : "创建失败");
    } finally {
      setSubmitting(false);
    }
  }
  async function control(id: string, action: string) {
    try {
      await api(`/workflows/jobs/${id}/${action}`, "POST", {});
      setJobs(await api<Job[]>("/workflows/jobs"));
    } catch (e) {
      fail(e instanceof Error ? e.message : "操作失败");
    }
  }
  const studyEstimate = resolved?.estimates.study;
  const skillEstimate = resolved?.estimates.skill;
  return (
    <main className="analysis-page">
      <div className="analysis-heading">
        <div>
          <div className="eyebrow">LEARN. DISTILL. REUSE.</div>
          <h1>从视频到知识，从知识到 Skill。</h1>
          <p>两步独立配置，也可以连起来一次完成。</p>
          <p className="analysis-caption">
            按 Joker UI 方法：主题知识 → 条件规则 → 能力指南 → 来源与覆盖校验。
          </p>
        </div>
        <span className="analysis-local">
          <WandSparkles size={15} />
          自动配置 · 随时调整
        </span>
      </div>
      <div className="workflow-modes" role="group" aria-label="选择任务类型">
        {(["study", "skill", "combined"] as Mode[]).map((mode) => (
          <button
            key={mode}
            aria-pressed={config.mode === mode}
            className={config.mode === mode ? "selected" : ""}
            onClick={() => edit({ mode })}
          >
            {mode === "study" ? (
              <BookOpen size={21} />
            ) : mode === "skill" ? (
              <Sparkles size={21} />
            ) : (
              <WandSparkles size={21} />
            )}
            <span>
              <b>{modeNames[mode]}</b>
              <small>
                {mode === "study"
                  ? "视频 → 有来源的学习笔记"
                  : mode === "skill"
                    ? "已有笔记 → 可复用的 Skill"
                    : "视频 → 学习笔记 → Skill"}
              </small>
            </span>
            {config.mode === mode && <Check size={15} />}
          </button>
        ))}
      </div>
      <div className="analysis-layout">
        <div className="analysis-form">
          <section className="analysis-section">
            <div className="section-title">
              <span>01</span>
              <h2>输入资料</h2>
            </div>
            <label className="workflow-toggle">
              <input
                type="checkbox"
                checked={config.auto_input}
                onChange={(e) => edit({ auto_input: e.target.checked })}
              />
              <span>
                自动发现输入资料
                <small>
                  视频使用已配置的视频目录；独立生成 Skill
                  时使用已审阅的课程笔记。手动输入优先。
                </small>
              </span>
            </label>
            {config.mode !== "skill" && (
              <div className="workflow-source">
                <h3>学习视频</h3>
                <div className="workflow-picker-actions">
                  <button
                    className="secondary"
                    disabled={picking}
                    onClick={() => pick("files")}
                  >
                    <Play size={14} />
                    选择视频文件
                  </button>
                  <button
                    className="secondary"
                    disabled={picking}
                    onClick={() => pick("folder")}
                  >
                    <FolderOpen size={14} />
                    选择视频文件夹
                  </button>
                </div>
                <textarea
                  aria-label="视频输入路径"
                  rows={3}
                  placeholder={
                    config.auto_input
                      ? defaults?.video_paths.join("\n") ||
                        "尚未配置视频目录，请选择文件或文件夹"
                      : "输入视频或文件夹路径，每行一个"
                  }
                  value={videoInput}
                  onChange={(e) => {
                    setVideoInput(e.target.value);
                    edit({ video_paths: lines(e.target.value) });
                  }}
                />
                <p className="analysis-caption">
                  支持单个视频、多选或递归扫描文件夹。自动发现的实际文件会在开始前列出。
                </p>
              </div>
            )}
            {config.mode !== "study" && (
              <div className="workflow-source">
                <h3>
                  {config.mode === "combined"
                    ? "补充已有笔记（可选）"
                    : "学习笔记"}
                </h3>
                {config.mode === "combined" && (
                  <p className="workflow-handoff">
                    <ArrowRight size={15} />
                    本次视频生成的笔记会自动传给第二步，无需重复选择。
                  </p>
                )}
                <div className="workflow-picker-actions">
                  <button
                    className="secondary"
                    disabled={picking}
                    onClick={() => pick("notes")}
                  >
                    <FileText size={14} />
                    选择笔记文件
                  </button>
                  <button
                    className="secondary"
                    disabled={picking}
                    onClick={() => pick("notes_folder")}
                  >
                    <FolderOpen size={14} />
                    选择笔记文件夹
                  </button>
                </div>
                <textarea
                  aria-label="笔记输入路径"
                  rows={3}
                  placeholder={
                    config.mode === "combined"
                      ? "可补充其他课程的笔记，每行一个路径"
                      : config.auto_input
                        ? `自动发现 ${defaults?.note_paths.length || 0} 份已审阅课程笔记；也可手动指定`
                        : "输入 Markdown / TXT 笔记或笔记文件夹，每行一个"
                  }
                  value={noteInput}
                  onChange={(e) => {
                    setNoteInput(e.target.value);
                    edit({ note_paths: lines(e.target.value) });
                  }}
                />
                <p className="analysis-caption">
                  文件夹自动识别
                  notes.md、notes-revised.md、document.md，排除撤回草稿与历史归档。
                </p>
              </div>
            )}
          </section>
          <section className="analysis-section">
            <div className="section-title">
              <span>02</span>
              <h2>每一步，用合适的模型</h2>
            </div>
            <div
              className={
                config.mode === "combined" ? "workflow-stage-grid" : ""
              }
            >
              {config.mode !== "skill" && (
                <ModelSettings
                  title="视频学习"
                  vision
                  value={config.study}
                  onChange={(study) => edit({ study })}
                />
              )}
              {config.mode !== "study" && (
                <ModelSettings
                  title="Skill 生成"
                  vision={false}
                  value={config.skill}
                  onChange={(skill) => edit({ skill })}
                />
              )}
            </div>
            <p className="analysis-caption">
              两步可以使用不同引擎、不同模型。只生成 Skill 不会重新处理视频。
            </p>
          </section>
          {config.mode !== "study" && (
            <section className="analysis-section">
              <div className="section-title">
                <span>03</span>
                <h2>你想得到怎样的 Skill</h2>
              </div>
              <label className="analysis-label">
                Skill 名称
                <input
                  aria-label="Skill 名称"
                  value={config.skill_name}
                  onChange={(e) => edit({ skill_name: e.target.value })}
                />
              </label>
              <label className="analysis-label">
                用途与范围
                <textarea
                  aria-label="Skill 用途"
                  rows={2}
                  value={config.skill_goal}
                  onChange={(e) => edit({ skill_goal: e.target.value })}
                />
              </label>
            </section>
          )}
          <section className="analysis-section">
            <div className="section-title">
              <span>{config.mode === "study" ? "03" : "04"}</span>
              <h2>输出位置</h2>
            </div>
            <label className="analysis-label">
              留空自动安排，也可指定一个根文件夹
              <div className="output-input">
                <input
                  aria-label="流程输出文件夹"
                  placeholder={
                    defaults?.output_directory || "自动使用项目学习产物目录"
                  }
                  value={config.output_directory}
                  onChange={(e) => edit({ output_directory: e.target.value })}
                />
                <button
                  className="secondary"
                  disabled={picking}
                  onClick={() => pick("output")}
                >
                  <FolderOpen size={14} />
                  浏览
                </button>
              </div>
            </label>
            <p className="analysis-caption">
              学习资料进入 notes/，Skill 进入
              skills/；每次任务独立保存，不覆盖已有内容。
            </p>
          </section>
        </div>
        <aside className="analysis-aside">
          <div className="estimate-card">
            <div className="estimate-title">
              <WandSparkles size={18} />
              <h2>本次运行配置</h2>
            </div>
            <p className="workflow-preview-intro">
              自动安排来源、输出与模型，然后核对本次真正会执行的内容。
            </p>
            <button
              className="secondary full"
              disabled={pending || submitting}
              onClick={resolve}
            >
              {pending ? (
                <LoaderCircle size={15} className="spin" />
              ) : (
                <WandSparkles size={15} />
              )}
              {pending ? "正在配置与预览…" : "自动配置并预览"}
            </button>
            {error && (
              <div role="alert" className="analysis-warning">
                {error}
              </div>
            )}
            {resolved ? (
              <>
                <div className="workflow-resolved">
                  {resolved.study && (
                    <div>
                      <b>视频学习</b>
                      <span>
                        {resolved.study.provider === "codex" ? (
                          <Cloud size={13} />
                        ) : (
                          <Cpu size={13} />
                        )}
                        {resolved.study.provider} · {resolved.study.model}
                      </span>
                      <small>
                        {resolved.videos.length} 个视频 · 共{" "}
                        {Math.round(
                          resolved.videos.reduce(
                            (n, v) => n + v.duration_ms,
                            0,
                          ) / 60000,
                        )}{" "}
                        分钟
                      </small>
                    </div>
                  )}
                  {resolved.skill && (
                    <div>
                      <b>Skill 生成</b>
                      <span>
                        {resolved.skill.provider === "codex" ? (
                          <Cloud size={13} />
                        ) : (
                          <Cpu size={13} />
                        )}
                        {resolved.skill.provider} · {resolved.skill.model}
                      </span>
                      <small>
                        {resolved.notes.length} 份已有笔记
                        {config.mode === "combined"
                          ? " + 本次学习结果自动衔接"
                          : ""}
                      </small>
                    </div>
                  )}
                  <div>
                    <b>输出根目录</b>
                    <small>{resolved.output_directory}</small>
                  </div>
                </div>
                <details className="workflow-file-preview">
                  <summary>查看本次输入清单</summary>
                  {resolved.video_paths.map((p) => (
                    <p key={p}>{p}</p>
                  ))}
                  {resolved.note_paths.map((p) => (
                    <p key={p}>{p}</p>
                  ))}
                </details>
                {resolved.errors.map((e) => (
                  <div className="analysis-warning" key={e.path}>
                    {e.path}：{e.error}
                  </div>
                ))}
                {studyEstimate && (
                  <div className="workflow-cost">
                    <b>视频学习基础用量</b>
                    {resolved.quality_profile && (
                      <small>
                        新增的知识覆盖核对与修订另计，暂无新版实测基准。
                      </small>
                    )}
                    <p>
                      {count(studyEstimate.total_tokens)} token ·{" "}
                      {money(studyEstimate.api_equivalent_usd)}
                    </p>
                    <small>
                      费用约 {studyEstimate.multiplier ?? "未知"}×，以 GPT-6.1
                      Sol 标准模式为 1×
                    </small>
                  </div>
                )}
                {skillEstimate && (
                  <div className="workflow-cost">
                    <b>Skill 生成预估</b>
                    <p>
                      {skillEstimate.usd_range
                        ? `${money(skillEstimate.usd_range[0])} – ${money(skillEstimate.usd_range[1])}`
                        : "待学习笔记生成后估算"}
                    </p>
                    {skillEstimate.token_range && (
                      <small>
                        {count(skillEstimate.token_range[0])} –{" "}
                        {count(skillEstimate.token_range[1])} token
                      </small>
                    )}
                    <small>{skillEstimate.basis}</small>
                  </div>
                )}
              </>
            ) : (
              <p className="start-hint">
                修改设置后，重新预览即可应用新的自动配置。
              </p>
            )}
            <button
              className="primary start-learning"
              disabled={
                !resolved || !!resolved.errors.length || pending || submitting
              }
              onClick={start}
            >
              {submitting ? (
                <LoaderCircle size={16} className="spin" />
              ) : (
                <Play size={16} />
              )}
              {submitting ? "正在创建…" : "开始" + modeNames[config.mode]}
            </button>
            <p className="estimate-source">
              金额是 API 等值估算，不代表订阅额度扣费。Skill
              生成尚无实测费用基准。
            </p>
          </div>
          <div className="learning-promise">
            <Check size={17} />
            <div>
              <b>生成与验证分别记录</b>
              <p>
                串联模式代表对本次素材发起综合。保留课程来源和适用边界，Skill
                生成后标记待验证，自动安装与发布另行操作。
              </p>
            </div>
          </div>
        </aside>
      </div>
      <section className="analysis-jobs">
        <div className="section-title">
          <h2>流程任务</h2>
          <button onClick={onLibrary}>
            打开视频资料库
            <ArrowRight size={14} />
          </button>
        </div>
        {jobsError && <div className="analysis-warning">{jobsError}</div>}
        {!jobs.length && !jobsError && (
          <div className="jobs-empty">
            两步都可以独立运行。选择模式并预览，开始一次新的学习。
          </div>
        )}
        {jobs.map((job) => (
          <div className="batch-card" key={job.id}>
            <header>
              <div>
                <span className="provider-pill">{modeNames[job.mode]}</span>
                <b>{statusNames[job.status] || job.status}</b>
                <small>
                  {new Date(job.created_at * 1000).toLocaleString("zh-CN")}
                </small>
              </div>
              <p>{job.config.output_directory}</p>
            </header>
            <div className="workflow-job-body">
              <p>
                {job.error ||
                  (job.stage.startsWith("extract:")
                    ? "正在从学习笔记提炼知识 · " + job.stage.slice(8)
                    : job.stage === "capability_mapping"
                      ? "正在建立能力路由与规则覆盖映射"
                      : job.stage.startsWith("capability:")
                        ? "正在融合能力指南并核对条件 · " + job.stage.slice(11)
                        : job.stage === "synthesize_skill"
                          ? "正在编写 Skill"
                          : job.stage === "complete"
                            ? "已完成生成"
                            : statusNames[job.status])}
              </p>
              {job.items.map((i) => (
                <p key={i.id}>
                  {i.path.split("/").pop()} ·{" "}
                  {i.run_error || i.run_status || "等待导入"}
                </p>
              ))}
              {!!job.skill_usage?.usage.input_tokens && (
                <p>
                  Skill 已记录{" "}
                  {count(
                    (job.skill_usage.usage.input_tokens || 0) +
                      (job.skill_usage.usage.output_tokens || 0),
                  )}{" "}
                  token · API 等值 {money(job.skill_usage.api_equivalent_usd)}
                  {job.skill_usage.unmetered_attempts
                    ? " · 部分调用用量未知"
                    : ""}
                </p>
              )}
              {job.result.skill_file && (
                <div className="workflow-skill-result">
                  <Sparkles size={15} />
                  <b>Skill 已生成 · 待验证</b>
                  {job.result.generation_report && (
                    <small>
                      保留 {job.result.generation_report.rule_count}{" "}
                      条有条件规则，形成{" "}
                      {job.result.generation_report.capability_count}{" "}
                      个能力模块；覆盖{" "}
                      {job.result.generation_report.source_count} 份来源。
                    </small>
                  )}
                  <code>{job.result.skill_file}</code>
                  <small>
                    结构检查已通过；实际任务效果尚未验证，未自动安装。
                  </small>
                </div>
              )}
              <div className="workflow-job-actions">
                {["queued", "learning", "running"].includes(job.status) && (
                  <button
                    className="secondary"
                    onClick={() => control(job.id, "pause")}
                  >
                    <Pause size={13} />
                    暂停流程
                  </button>
                )}
                {["failed", "paused", "cancelled"].includes(job.status) && (
                  <button
                    className="secondary"
                    onClick={() => control(job.id, "retry")}
                  >
                    <Play size={13} />
                    继续 / 重试
                  </button>
                )}
                {["queued", "learning", "running", "paused"].includes(
                  job.status,
                ) && (
                  <button
                    className="secondary"
                    onClick={() => control(job.id, "cancel")}
                  >
                    <X size={13} />
                    取消
                  </button>
                )}
              </div>
            </div>
          </div>
        ))}
      </section>
    </main>
  );
}
