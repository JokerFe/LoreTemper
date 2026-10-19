import React, { useState, useEffect, useRef, useCallback } from "react";
import { createRoot } from "react-dom/client";
import {
  Film,
  BookOpen,
  Search,
  Settings,
  FolderPlus,
  ArrowUpRight,
  Play,
  Pause,
  RotateCcw,
  X,
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  FileText,
  Images,
  Clock3,
  HardDrive,
  CircleHelp,
  LoaderCircle,
  MonitorPlay,
  ShieldCheck,
  AlertCircle,
  Save,
  ExternalLink,
} from "lucide-react";
import "./style.css";
import { AnalysisPage } from "./AnalysisPage";
import { WorkflowsPage } from "./WorkflowsPage";

type Dict = Record<string, any>;
async function api(path: string, method = "GET", body?: any) {
  const r = await fetch("/api/v1" + path, {
    method,
    headers: { "Content-Type": "application/json", "X-VT-Request": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({}));
    throw new Error(e.message || e.detail || "请求失败");
  }
  return r.json();
}
const time = (ms: number = 0) => {
  const s = Math.floor(ms / 1000);
  return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60]
    .map((x) => String(x).padStart(2, "0"))
    .join(":");
};
const stateNames: Dict = {
  queued: "等待处理",
  running: "正在学习",
  pause_requested: "正在暂停",
  paused: "已暂停",
  cancel_requested: "正在取消",
  cancelled: "已取消",
  failed: "处理失败",
  succeeded: "学习完成",
  succeeded_with_warnings: "待核实",
  undefined: "未处理",
};
const stages: Dict = {
  queued: "队列中",
  probe: "检查视频",
  extract_audio: "提取音频",
  transcribe: "语音转写",
  select_frames: "筛选画面",
  analyze_frames: "理解画面",
  thematic_study: "按主题取证",
  fuse: "整理操作步骤",
  summarize: "生成学习笔记",
  validate: "核对证据",
  export: "导出资料",
  index: "建立搜索索引",
  prepare_playback: "准备播放文件",
  complete: "已完成",
};

function App() {
  const [assets, setAssets] = useState<Dict[]>([]);
  const [health, setHealth] = useState<Dict>({});
  const [page, setPage] = useState(
    new URLSearchParams(location.search).get("view") === "study-advanced"
      ? "advanced"
      : new URLSearchParams(location.search).get("view") === "analysis"
        ? "analysis"
        : "library",
  );
  const [selected, setSelected] = useState<Dict | null>(null);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [ready, setReady] = useState(false);
  const [authError, setAuthError] = useState("");
  const [modal, setModal] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Dict[] | null>(null);
  const refresh = useCallback(async () => {
    const [a, h] = await Promise.all([api("/assets"), api("/health")]);
    setAssets(a);
    setHealth(h);
  }, []);
  useEffect(() => {
    (async () => {
      try {
        const p = new URLSearchParams(location.hash.slice(1));
        if (p.has("token")) {
          await api("/session", "POST", { token: p.get("token") });
          history.replaceState(null, "", location.pathname);
        }
        await refresh();
        setReady(true);
      } catch (e: any) {
        setAuthError(e.message);
      }
    })();
  }, [refresh]);
  useEffect(() => {
    if (!ready) return;
    const timer = setInterval(() => {
      if (!document.hidden) refresh().catch((e) => setError(e.message));
    }, 2500);
    return () => clearInterval(timer);
  }, [ready, refresh]);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(""), 5000);
    return () => clearTimeout(t);
  }, [toast]);
  useEffect(() => {
    if (!query.trim()) {
      setResults(null);
      return;
    }
    const t = setTimeout(
      () =>
        api("/search?q=" + encodeURIComponent(query))
          .then(setResults)
          .catch((e) => setError(e.message)),
      350,
    );
    return () => clearTimeout(t);
  }, [query]);
  async function action(runId: string, action: string) {
    try {
      await api(`/runs/${runId}/${action}`, "POST", {});
      await refresh();
      setToast("任务状态已更新");
    } catch (e: any) {
      setError(e.message);
    }
  }
  const complete = assets.filter((a) =>
    a.status?.startsWith("succeeded"),
  ).length;
  const busy = assets.filter((a) =>
    ["running", "queued"].includes(a.status),
  ).length;
  if (!ready)
    return (
      <div className="gate">
        <div className="brand-mark">
          <Film />
        </div>
        <h1>映知</h1>
        <p>{authError || "正在打开你的本地学习空间…"}</p>
        {authError && (
          <p className="muted">
            请双击项目中的「启动映知.command」，使用启动器给出的链接。
          </p>
        )}
      </div>
    );
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a
          className="brand"
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setPage("library");
            setSelected(null);
          }}
        >
          <span className="brand-mark">
            <Film size={24} />
          </span>
          <div>
            <b>映知</b>
            <small>VIDEO STUDY</small>
          </div>
        </a>
        <div className="nav-label">我的工作空间</div>
        <nav>
          <button
            className={page === "analysis" ? "active" : ""}
            onClick={() => {
              setPage("analysis");
              setSelected(null);
            }}
          >
            <Play size={18} />
            新建学习
          </button>
          <button
            className={page === "library" ? "active" : ""}
            onClick={() => {
              setPage("library");
              setSelected(null);
            }}
          >
            <BookOpen size={18} />
            视频资料库<span>{assets.length}</span>
          </button>
          <button
            className={page === "settings" ? "active" : ""}
            onClick={() => {
              setPage("settings");
              setSelected(null);
            }}
          >
            <Settings size={18} />
            偏好设置
          </button>
        </nav>
        <div className="side-card">
          <ShieldCheck size={22} />
          <b>由你选择学习方式</b>
          <p>
            本地 Ollama 或 Codex 订阅，
            <br />
            每次学习独立选择。
          </p>
          <span className="local-label">
            <i />
            本地运行
          </span>
        </div>
        <footer>
          <span className={"dot " + (health.worker_alive ? "online" : "")} />
          {health.worker_alive ? "学习引擎已连接" : "学习引擎未启动"}
          <small>映知 v0.1 · macOS</small>
        </footer>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <span className="breadcrumb">
            工作空间 <ChevronRight size={14} />{" "}
            {page === "analysis"
              ? "新建学习"
              : page === "settings"
                ? "偏好设置"
                : selected
                  ? "视频详情"
                  : "视频资料库"}
          </span>
          <span className="device">
            <HardDrive size={14} /> 本地工作台
          </span>
        </header>
        {error && (
          <div className="banner error">
            <AlertCircle size={18} />
            <span>{error}</span>
            <button aria-label="关闭错误" onClick={() => setError("")}>
              <X size={16} />
            </button>
          </div>
        )}
        {health.waiting_reason && (
          <div className="banner">
            <Clock3 size={18} />
            {health.waiting_reason}，队列将在条件满足后继续。
          </div>
        )}
        {page === "analysis" ? (
          <WorkflowsPage
            notify={setToast}
            fail={setError}
            onLibrary={() => setPage("library")}
          />
        ) : page === "advanced" ? (
          <AnalysisPage
            notify={setToast}
            fail={setError}
            onLibrary={() => setPage("library")}
          />
        ) : page === "settings" ? (
          <SettingsPage notify={setToast} fail={setError} />
        ) : selected ? (
          <Detail
            asset={selected}
            onBack={() => setSelected(null)}
            action={action}
            notify={setToast}
            fail={setError}
          />
        ) : (
          <main className="library">
            <div className="page-heading">
              <div>
                <div className="eyebrow">WATCH LESS. LEARN MORE.</div>
                <h1>让每段视频，成为你的知识。</h1>
                <p>从录屏到图文笔记，留住每一个值得学习的细节。</p>
              </div>
              <div className="actions">
                <button className="secondary" onClick={() => setModal(true)}>
                  <FolderPlus size={18} />
                  导入视频
                </button>
                <button className="primary" onClick={() => setPage("analysis")}>
                  <Play size={17} />
                  新建学习
                </button>
              </div>
            </div>
            <div className="stats">
              <div>
                <span className="stat-icon">
                  <Film size={22} />
                </span>
                <div>
                  <small>全部视频</small>
                  <strong>
                    {assets.length}
                    <em>个</em>
                  </strong>
                </div>
              </div>
              <div>
                <span className="stat-icon mint">
                  <BookOpen size={22} />
                </span>
                <div>
                  <small>已生成笔记</small>
                  <strong>
                    {complete}
                    <em>份</em>
                  </strong>
                </div>
              </div>
              <div>
                <span className="stat-icon sand">
                  <Clock3 size={22} />
                </span>
                <div>
                  <small>处理队列</small>
                  <strong>
                    {busy}
                    <em>个</em>
                  </strong>
                </div>
              </div>
            </div>
            <div className="section-top">
              <h2>
                我的视频 <span>{assets.length}</span>
              </h2>
              <label className="search">
                <Search size={17} />
                <input
                  aria-label="搜索视频和笔记"
                  placeholder="搜索视频、操作步骤、画面文字…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                {query && (
                  <button aria-label="清空搜索" onClick={() => setQuery("")}>
                    <X size={15} />
                  </button>
                )}
              </label>
            </div>
            {results ? (
              <div className="search-results">
                <p className="muted">找到 {results.length} 条相关证据</p>
                {results.map((r) => (
                  <button
                    key={r.id}
                    onClick={() => {
                      const a = assets.find((x) => x.id === r.asset_id);
                      if (a)
                        setSelected({
                          ...a,
                          seek_ms: r.start_ms,
                          run_id: r.run_id,
                        });
                    }}
                  >
                    <b>
                      {r.title}
                      <span>{time(r.start_ms)}</span>
                    </b>
                    <p>{r.raw_text.slice(0, 250)}</p>
                    <ArrowUpRight size={18} />
                  </button>
                ))}
              </div>
            ) : assets.length ? (
              <div className="asset-grid">
                {assets.map((a) => (
                  <article className="asset-card" key={a.id}>
                    <button
                      className="asset-cover"
                      onClick={() => setSelected(a)}
                    >
                      <MonitorPlay size={44} strokeWidth={1.3} />
                      <span className={"badge " + a.status}>
                        {stateNames[a.status]}
                      </span>
                      <span className="cover-label">SCREEN RECORDING</span>
                      <span className="play-circle">
                        <Play size={18} />
                      </span>
                    </button>
                    <div className="asset-body">
                      <button
                        className="asset-title"
                        onClick={() => setSelected(a)}
                      >
                        {a.title}
                      </button>
                      <p>
                        {a.media_json
                          ? time(JSON.parse(a.media_json).duration_ms)
                          : "待检查"}
                        <span>·</span>
                        {(a.size_bytes / 1e6).toFixed(1)} MB
                      </p>
                      {a.status === "running" ? (
                        <div className="progress-label">
                          <LoaderCircle size={14} className="spin" />
                          {stages[a.stage] || a.stage}
                          {a.progress_total > 0 && (
                            <span>
                              {a.progress_done}/{a.progress_total}
                            </span>
                          )}
                        </div>
                      ) : a.error ? (
                        <p className="card-error" title={a.error}>
                          {a.error}
                        </p>
                      ) : (
                        <div className="card-bottom">
                          <span>
                            {new Date(a.created_at * 1000).toLocaleDateString(
                              "zh-CN",
                            )}
                          </span>
                          <button onClick={() => setSelected(a)}>
                            查看详情
                            <ArrowUpRight size={15} />
                          </button>
                        </div>
                      )}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="empty">
                <div className="empty-art">
                  <div className="paper back" />
                  <div className="paper">
                    <MonitorPlay size={45} strokeWidth={1.2} />
                    <i />
                    <i />
                    <i />
                  </div>
                  <span className="spark">✧</span>
                </div>
                <h3>你的第一份视频笔记，从这里开始</h3>
                <p>
                  导入课程录屏或操作教程，映知会结合语音和画面，
                  <br />
                  整理出带截图、时间戳的学习笔记。
                </p>
                <button className="primary" onClick={() => setModal(true)}>
                  <FolderPlus size={18} />
                  选择视频目录
                </button>
                <small>支持 MP4、MOV、MKV、WebM 等本地视频</small>
              </div>
            )}
            <div className="process-strip">
              <span>
                <span>01</span>导入本地视频
              </span>
              <ChevronRight size={16} />
              <span>
                <span>02</span>理解语音与画面
              </span>
              <ChevronRight size={16} />
              <span>
                <span>03</span>收获图文笔记
              </span>
            </div>
          </main>
        )}
      </div>
      {modal && (
        <ImportModal
          close={() => setModal(false)}
          done={() => {
            setModal(false);
            setToast("目录已添加，新视频稳定后将自动进入队列");
            refresh();
          }}
          fail={setError}
        />
      )}{" "}
      {toast && (
        <div className="toast">
          <Check size={18} />
          {toast}
        </div>
      )}
    </div>
  );
}

function ImportModal({ close, done, fail }: any) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: any) {
    e.preventDefault();
    setBusy(true);
    try {
      const s = await api("/settings");
      s.directories = [...new Set([...s.directories, path.trim()])];
      await api("/settings", "PUT", s);
      await api("/imports", "POST", { path: path.trim() });
      done();
    } catch (e: any) {
      fail(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div
      className="modal-overlay"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="import-heading"
      >
        <button className="modal-close" aria-label="关闭导入" onClick={close}>
          <X />
        </button>
        <span className="modal-icon">
          <FolderPlus size={28} />
        </span>
        <h2 id="import-heading">添加视频目录</h2>
        <p>
          映知会读取这个目录中的视频，之后放入的新视频也会自动处理。原文件不会被修改。
        </p>
        <form onSubmit={submit}>
          <label>
            本地文件夹路径
            <input
              autoFocus
              required
              placeholder="例如 /Users/你的名字/Movies/教程"
              value={path}
              onChange={(e) => setPath(e.target.value)}
            />
          </label>
          <div className="helper">
            在 Finder 中选中文件夹，按 ⌥⌘C 复制路径。
          </div>
          <button className="primary full" disabled={busy || !path.trim()}>
            {busy ? (
              <LoaderCircle className="spin" size={18} />
            ) : (
              <FolderPlus size={18} />
            )}
            添加并扫描
          </button>
        </form>
        <div className="privacy">
          <ShieldCheck size={16} />
          只在本机读取与分析，无需上传视频
        </div>
      </section>
    </div>
  );
}

function SettingsPage({ notify, fail }: any) {
  const [s, setS] = useState<Dict | null>(null);
  const [doctor, setDoctor] = useState<Dict | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api("/settings")
      .then(setS)
      .catch((e) => fail(e.message));
    api("/doctor")
      .then(setDoctor)
      .catch((e) => fail(e.message));
  }, []);
  if (!s)
    return (
      <div className="loading">
        <LoaderCircle className="spin" />
        加载设置
      </div>
    );
  const field = (key: string, value: any) => setS({ ...s, [key]: value });
  async function save() {
    setBusy(true);
    try {
      await api("/settings", "PUT", s);
      notify("设置已保存，新的任务会使用这些设置");
    } catch (e: any) {
      fail(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <main className="settings-page">
      <div className="page-heading">
        <div>
          <div className="eyebrow">YOUR LOCAL WORKSPACE</div>
          <h1>按你的节奏学习</h1>
          <p>管理视频目录、处理偏好和本地模型。</p>
        </div>
        <button className="primary" onClick={save} disabled={busy}>
          <Save size={18} />
          保存设置
        </button>
      </div>
      <section className="settings-card">
        <h2>
          <HardDrive size={20} />
          环境状态
        </h2>
        <div className="checks">
          {doctor?.checks.map((c) => (
            <div key={c.name}>
              <span className={c.ok ? "check-ok" : "check-bad"}>
                {c.ok ? <Check size={16} /> : <AlertCircle size={16} />}
              </span>
              <b>{c.name}</b>
              <small title={c.detail}>{c.detail}</small>
            </div>
          )) || <p>正在检查模型…</p>}
        </div>
      </section>
      <section className="settings-card">
        <h2>
          <FolderPlus size={20} />
          视频目录
        </h2>
        <label>
          每行一个目录
          <textarea
            rows={4}
            value={s.directories.join("\n")}
            onChange={(e) =>
              field("directories", e.target.value.split("\n").filter(Boolean))
            }
            placeholder="/Users/你的名字/Movies/教程"
          />
        </label>
        <label className="toggle">
          <input
            type="checkbox"
            checked={s.auto_import}
            onChange={(e) => field("auto_import", e.target.checked)}
          />
          <span>自动发现目录中的新视频</span>
        </label>
      </section>
      <section className="settings-card">
        <h2>
          <Settings size={20} />
          处理偏好
        </h2>
        <div className="form-grid">
          <label>
            画面分析模式
            <select
              value={s.profile}
              onChange={(e) => field("profile", e.target.value)}
            >
              <option value="balanced">均衡 · 日常录屏</option>
              <option value="precise">精细 · 密集操作</option>
              <option value="fast">快速 · 稀疏画面</option>
            </select>
          </label>
          <label>
            语音语言
            <select
              value={s.language}
              onChange={(e) => field("language", e.target.value)}
            >
              <option value="auto">自动识别</option>
              <option value="zh">中文</option>
              <option value="en">英文</option>
              <option value="ja">日语</option>
            </select>
          </label>
          <label>
            语音转写引擎
            <select
              value={s.asr_backend}
              onChange={(e) => field("asr_backend", e.target.value)}
            >
              <option value="whisper_cpp">whisper.cpp · Apple GPU</option>
              <option value="faster_whisper">faster-whisper · CPU</option>
            </select>
          </label>
          <label>
            每分钟画面预算
            <input
              type="number"
              min={4}
              max={180}
              value={s.frames_per_minute}
              onChange={(e) =>
                field("frames_per_minute", Number(e.target.value))
              }
            />
          </label>
        </div>
        <label>
          专业术语（逗号分隔）
          <input
            value={s.glossary.join(", ")}
            onChange={(e) =>
              field(
                "glossary",
                e.target.value
                  .split(/[,，]/)
                  .map((x) => x.trim())
                  .filter(Boolean),
              )
            }
            placeholder="产品名称、专业术语、人名"
          />
        </label>
        <label className="toggle">
          <input
            type="checkbox"
            checked={s.require_ac_power}
            onChange={(e) => field("require_ac_power", e.target.checked)}
          />
          <span>仅在接通电源时启动新的分析任务</span>
        </label>
      </section>
      <section className="settings-card">
        <h2>
          <MonitorPlay size={20} />
          本地模型
        </h2>
        <div className="form-grid">
          <label>
            视觉模型
            <input
              value={s.vision_model}
              onChange={(e) => field("vision_model", e.target.value)}
            />
          </label>
          <label>
            文本模型
            <input
              value={s.text_model}
              onChange={(e) => field("text_model", e.target.value)}
            />
          </label>
        </div>
        <label>
          Whisper 模型文件
          <input
            value={s.whisper_model_path}
            onChange={(e) => field("whisper_model_path", e.target.value)}
          />
        </label>
        <p className="helper">
          模型需要提前下载。应用只连接本机 Ollama，模型按阶段加载。
        </p>
      </section>
    </main>
  );
}

function Detail({ asset, onBack, action, notify, fail }: any) {
  const [activeRunId, setActiveRunId] = useState(asset.run_id);
  const [versions, setVersions] = useState<Dict[]>([]);
  useEffect(() => {
    api("/assets/" + asset.id)
      .then((a) => setVersions(a.runs || []))
      .catch(() => {});
  }, [asset.id]);
  const [run, setRun] = useState<Dict | null>(null);
  const [doc, setDoc] = useState<Dict | null>(null);
  const [tab, setTab] = useState("notes");
  const [notes, setNotes] = useState("");
  const [preview, setPreview] = useState<Dict | null>(null);
  const [events, setEvents] = useState<Dict[]>([]);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const video = useRef<HTMLVideoElement>(null);
  const edited = useRef(false);
  useEffect(() => {
    let alive = true;
    async function refresh() {
      try {
        if (!activeRunId) return;
        const r = await api("/runs/" + activeRunId);
        if (!alive) return;
        setRun(r);
        setEvents(await api("/runs/" + activeRunId + "/events"));
        if (r.status.startsWith("succeeded")) {
          const d = await api("/runs/" + activeRunId + "/documents/latest");
          if (alive) {
            setDoc(d);
            if (!edited.current) setNotes(d.body.user_notes || "");
          }
        }
      } catch (e: any) {
        if (alive) fail(e.message);
      }
    }
    refresh();
    const t = setInterval(refresh, 3000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [activeRunId]);
  const jump = (ms: number) => {
    if (video.current) {
      video.current.currentTime = Math.max(0, ms / 1000 - 1);
      video.current.play().catch(() => {});
    }
  };
  const body = doc?.body;
  async function rerun() {
    try {
      const payload: Dict = { profile: "precise" };
      if (start) payload.start_ms = Math.round(Number(start) * 1000);
      if (end) payload.end_ms = Math.round(Number(end) * 1000);
      await api(`/assets/${asset.id}/runs`, "POST", payload);
      notify("已创建精细分析任务，原笔记会保留");
      onBack();
    } catch (e: any) {
      fail(e.message);
    }
  }
  async function saveNotes() {
    try {
      await api(`/runs/${activeRunId}/documents/edit`, "POST", {
        base_version: doc?.version,
        user_notes: notes,
      });
      edited.current = false;
      setDoc(await api("/runs/" + activeRunId + "/documents/latest"));
      notify("人工笔记已保存");
    } catch (e: any) {
      fail(e.message);
    }
  }
  async function download() {
    try {
      const out = await api(`/runs/${activeRunId}/exports/create`, "POST", {});
      const id = Object.entries(out).find(([name]) =>
        name.endsWith(".zip"),
      )?.[1];
      if (id) window.location.href = "/artifacts/" + id;
    } catch (e: any) {
      fail(e.message);
    }
  }
  return (
    <main className="detail">
      <button className="back" onClick={onBack}>
        <ChevronLeft size={18} />
        返回资料库
      </button>
      <div className="detail-heading">
        <div>
          <h1>{body?.overview.title || asset.title}</h1>
          <p className="muted">
            {stateNames[run?.status || asset.status]} ·{" "}
            {stages[run?.stage] || "等待处理"}
          </p>
        </div>
        <div className="actions">
          {run && ["running", "queued"].includes(run.status) && (
            <button onClick={() => action(run.id, "pause")}>
              <Pause size={16} />
              暂停
            </button>
          )}
          {run && ["failed", "paused", "cancelled"].includes(run.status) && (
            <button onClick={() => action(run.id, "retry")}>
              <RotateCcw size={16} />
              继续处理
            </button>
          )}
          {run &&
            ["running", "queued", "paused", "pause_requested"].includes(
              run.status,
            ) && (
              <button onClick={() => action(run.id, "cancel")}>
                <X size={16} />
                取消任务
              </button>
            )}
          {body && (
            <button className="primary" onClick={download}>
              <Download size={17} />
              导出笔记
            </button>
          )}
        </div>
      </div>
      {run?.error && (
        <div className="banner error">
          <AlertCircle size={18} />
          {run.error}
        </div>
      )}
      {versions.length > 1 && (
        <label className="version-select">
          分析版本
          <select
            value={activeRunId}
            onChange={(e) => {
              setActiveRunId(e.target.value);
              setDoc(null);
              edited.current = false;
            }}
          >
            {versions.map((v) => (
              <option key={v.id} value={v.id}>
                {new Date(v.created_at * 1000).toLocaleString()} ·{" "}
                {stateNames[v.status]}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="detail-grid">
        <aside className="video-column">
          <div className="player">
            <video
              ref={video}
              controls
              src={"/media/assets/" + asset.id}
              onLoadedMetadata={() => {
                if (asset.seek_ms) jump(asset.seek_ms);
              }}
            />
          </div>
          <div className="video-caption">
            <Film size={16} />
            {asset.title}
          </div>
          <div className="panel">
            <h3>章节导航</h3>
            {body?.chapters.map((c, i) => (
              <button
                className="chapter-link"
                key={i}
                onClick={() => jump(c.steps[0]?.start_ms || 0)}
              >
                <span>{String(i + 1).padStart(2, "0")}</span>
                {c.title}
                <small>{time(c.steps[0]?.start_ms || 0)}</small>
              </button>
            )) || <p className="muted">分析完成后会自动生成章节。</p>}
          </div>
          <div className="panel">
            <h3>精细分析片段</h3>
            <p className="helper">只分析需要复查的时间段，保留原结果。</p>
            <div className="range-fields">
              <input
                aria-label="开始秒数"
                placeholder="开始（秒）"
                type="number"
                min="0"
                value={start}
                onChange={(e) => setStart(e.target.value)}
              />
              <span>—</span>
              <input
                aria-label="结束秒数"
                placeholder="结束（秒）"
                type="number"
                min="0"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
              />
            </div>
            <button className="secondary full" onClick={rerun}>
              <RotateCcw size={15} />
              创建精细分析
            </button>
          </div>
        </aside>
        <section className="notes-column">
          <div className="tabs">
            {[
              ["notes", "图文笔记", BookOpen],
              ["transcript", "完整字幕", FileText],
              ["frames", "截图证据", Images],
              ["activity", "处理记录", Clock3],
            ].map(([id, label, Icon]: any) => (
              <button
                key={id}
                className={tab === id ? "active" : ""}
                onClick={() => setTab(id)}
              >
                <Icon size={16} />
                {label}
              </button>
            ))}
          </div>
          {tab === "activity" ? (
            <div className="panel events">
              {events.map((e) => (
                <div key={e.seq}>
                  <span>
                    {new Date(e.created_at * 1000).toLocaleTimeString()}
                  </span>
                  <p>{stages[e.message] || e.message}</p>
                </div>
              ))}
            </div>
          ) : !body ? (
            <div className="waiting">
              <LoaderCircle
                className={run?.status === "running" ? "spin" : ""}
                size={36}
              />
              <h3>{stages[run?.stage] || "等待视频分析"}</h3>
              <p>
                {run?.progress_total
                  ? `已完成 ${run.progress_done} / ${run.progress_total}`
                  : "每个已完成片段都会保存，稍后可以回来查看。"}
              </p>
            </div>
          ) : tab === "transcript" ? (
            <div className="panel transcript">
              {body.transcript.length ? (
                body.transcript.map((t) => (
                  <button key={t.id} onClick={() => jump(t.start_ms)}>
                    <span>{time(t.start_ms)}</span>
                    <p>{t.raw_text}</p>
                  </button>
                ))
              ) : (
                <p className="muted">没有可用语音，本次依据画面分析。</p>
              )}
            </div>
          ) : tab === "frames" ? (
            <div className="frame-grid">
              {Object.values(body.frames).map((f: any) => (
                <button key={f.id} onClick={() => setPreview(f)}>
                  <img loading="lazy" src={"/artifacts/" + f.artifact_id} />
                  <span>
                    {time(f.time_ms)}
                    {f.parent_id ? " · 局部裁切" : ""}
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <div className="document">
              <div className="summary-box">
                <div className="eyebrow">视频概览</div>
                <p>{body.overview.summary}</p>
                <small>
                  依据 {body.overview.summary_evidence_ids?.length || 0}{" "}
                  条原始证据生成
                </small>
              </div>
              {body.warnings.length > 0 && (
                <div className="warning-box">
                  <CircleHelp size={18} />
                  <div>
                    {body.warnings.map((w, i) => (
                      <p key={i}>{w}</p>
                    ))}
                  </div>
                </div>
              )}
              {[
                ["goals", "学习目标"],
                ["prerequisites", "前置条件"],
                ["key_points", "核心要点"],
                ["verification", "验证方式"],
              ].map(
                ([key, label]) =>
                  body.overview[key]?.length > 0 && (
                    <section className="knowledge" key={key}>
                      <h3>{label}</h3>
                      <ul>
                        {body.overview[key].map((p, i) => (
                          <li key={i}>
                            {p.text}
                            <Evidence
                              ids={p.evidence_ids}
                              body={body}
                              jump={jump}
                              preview={setPreview}
                            />
                          </li>
                        ))}
                      </ul>
                    </section>
                  ),
              )}
              {body.notes_html && (
                <section
                  className="thematic-content"
                  dangerouslySetInnerHTML={{ __html: body.notes_html }}
                />
              )}
              {body.chapters.map((c, ci) => (
                <section className="chapter" key={ci}>
                  <h2>
                    <span>{String(ci + 1).padStart(2, "0")}</span>
                    {c.title}
                  </h2>
                  <p className="chapter-summary">{c.summary}</p>
                  {c.steps.map((s, i) => (
                    <article className="step" key={i}>
                      <div className="step-header">
                        <h3>{s.title}</h3>
                        <button
                          className="time-link"
                          onClick={() => jump(s.start_ms)}
                        >
                          <Play size={12} />
                          {time(s.start_ms)}
                        </button>
                      </div>
                      <p>{s.action}</p>
                      {s.before_state && (
                        <p className="state-text">操作前：{s.before_state}</p>
                      )}
                      {s.after_state && (
                        <div className="result">
                          <Check size={16} />
                          {s.after_state}
                        </div>
                      )}
                      {s.parameters?.map((p, j) => (
                        <div className="parameter" key={j}>
                          <b>{p.name}</b>
                          <code>{p.value ?? "无法确认"}</code>
                          {p.uncertainty && <small>{p.uncertainty}</small>}
                        </div>
                      ))}
                      <Evidence
                        ids={s.evidence_ids}
                        body={body}
                        jump={jump}
                        preview={setPreview}
                      />
                      <div className="step-images">
                        {s.evidence_ids
                          .filter((id) => body.frames[id])
                          .slice(0, 2)
                          .map((id) => (
                            <button
                              key={id}
                              onClick={() => setPreview(body.frames[id])}
                            >
                              <img
                                loading="lazy"
                                src={
                                  "/artifacts/" + body.frames[id].artifact_id
                                }
                              />
                              <span>
                                {time(body.frames[id].time_ms)} · 查看原图
                                <ExternalLink size={12} />
                              </span>
                            </button>
                          ))}
                      </div>
                      {s.uncertainties?.map((u, j) => (
                        <p className="uncertain" key={j}>
                          <CircleHelp size={14} />
                          {u}
                        </p>
                      ))}
                      {s.evidence_type === "inferred" && (
                        <p className="uncertain">
                          此步骤包含推断，需回看原视频核实。
                        </p>
                      )}
                    </article>
                  ))}
                  {c.unresolved_items.map((u, i) => (
                    <p key={i} className="uncertain">
                      <CircleHelp size={14} />
                      {u}
                    </p>
                  ))}
                </section>
              ))}
              {body.overview.unresolved_items?.length > 0 && (
                <section className="knowledge">
                  <h3>待核实项</h3>
                  <ul>
                    {body.overview.unresolved_items.map((u, i) => (
                      <li key={i}>{u}</li>
                    ))}
                  </ul>
                </section>
              )}
              <section className="manual-notes">
                <h3>我的补充笔记</h3>
                <textarea
                  rows={5}
                  value={notes}
                  onChange={(e) => {
                    edited.current = true;
                    setNotes(e.target.value);
                  }}
                  placeholder="记录你的理解、修正或实践结果…"
                />
                <button className="secondary" onClick={saveNotes}>
                  <Save size={15} />
                  保存笔记
                </button>
              </section>
            </div>
          )}
        </section>
      </div>
      {preview && (
        <div className="image-overlay" onClick={() => setPreview(null)}>
          <button aria-label="关闭图片">
            <X />
          </button>
          <img src={"/artifacts/" + preview.artifact_id} />
          <span>
            {time(preview.time_ms)} · {preview.id}
          </span>
        </div>
      )}
    </main>
  );
}
function Evidence({ ids, body, jump, preview }: any) {
  return (
    <div className="evidence">
      {ids.map((id) => {
        const f = body.frames[id];
        const t = body.transcript.find((x) => x.id === id);
        return (
          <button
            key={id}
            onClick={() => (f ? preview(f) : t && jump(t.start_ms))}
            title={t?.raw_text || id}
          >
            {f ? <Images size={12} /> : <FileText size={12} />}{" "}
            {time(f?.time_ms || t?.start_ms || 0)}
          </button>
        );
      })}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
