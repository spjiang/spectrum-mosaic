const TOKEN_KEY = "mosaic_token";
const USER_KEY = "mosaic_user";
const ROLES_KEY = "mosaic_roles";

/** 凭证失效时清掉本地登录态并回到登录页，避免停在空白页上弹错误。 */
function redirectToLogin() {
  setToken(null);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(ROLES_KEY);
  if (!window.location.pathname.startsWith("/login")) {
    window.location.replace("/login");
  }
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(t: string | null) {
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!(init.body instanceof FormData) && init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, { ...init, headers });
  if (res.status === 401) {
    redirectToLogin();
    return new Promise(() => {});
  }
  if (!res.ok) {
    const text = await res.text();
    let msg = text || res.statusText;
    try {
      const j = JSON.parse(text);
      if (typeof j.detail === "string") msg = j.detail;
    } catch {
      /* keep raw */
    }
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  return (await res.text()) as T;
}

async function openAuthedFile(path: string, fallbackName: string) {
  const headers = new Headers();
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(path, { headers });
  if (res.status === 401) {
    redirectToLogin();
    return;
  }
  if (!res.ok) {
    const text = await res.text();
    let msg = text || res.statusText;
    try {
      const j = JSON.parse(text);
      if (typeof j.detail === "string") msg = j.detail;
    } catch {
      /* keep raw */
    }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const opened = window.open(url, "_blank", "noopener");
  if (!opened) {
    const a = document.createElement("a");
    a.href = url;
    a.download = fallbackName;
    a.click();
  }
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export type InspectBand = {
  name: string;
  wavelength_nm?: number | null;
};

export type InspectMeta = {
  id: string;
  filename: string;
  format?: string;
  width: number;
  height: number;
  count: number;
  dtype?: string;
  size_bytes?: number;
  nodata?: unknown;
  stats_sampled?: boolean;
  bands: InspectBand[];
  geotransform?: { origin_x: number; origin_y: number; pixel_w: number; pixel_h: number } | null;
  xmp?: { raw?: string; tags?: { key: string; name?: string; value: string }[] };
  exif?: Record<string, string>;
  tiff?: { compression?: string; tags?: { key: string; name?: string; value: string }[] } | null;
};

export type InspectPixel = {
  col: number;
  row: number;
  values: Array<number | string | null>;
  bands?: { name: string; value: number | string | null; wavelength_nm?: number | null }[];
  hex?: string | null;
  x?: number | null;
  y?: number | null;
};

function readError(text: string, fallback: string) {
  let msg = text || fallback;
  try {
    const j = JSON.parse(text);
    if (typeof j.detail === "string") msg = j.detail;
  } catch {
    /* 保留原文 */
  }
  return msg;
}

export type StageStep = {
  name: string;
  detail: string;
  elapsed_s: number | null;
  status: "done" | "running";
};

export type IncrementalProject = {
  id: string;
  name: string;
  input_dir: string;
  inbox_dir: string;
  output_root: string;
  auto_run: boolean;
  scan_interval_minutes: number;
  latest_shot_index?: number | null;
  latest_filename?: string | null;
  current_output_dir?: string | null;
  created_at?: string | null;
};

export type IncrementalDetail = IncrementalProject & {
  busy: boolean;
  blocked_by_other: boolean;
  has_new_files: boolean;
  waiting_for_job: boolean;
  current_job_id?: string | null;
  latest_ingested_at?: string | null;
  last_scan_at?: string | null;
  incoming: number;
  ready: number;
  ingested: number;
  started_job_id?: string | null;
  conflicts?: string[];
  job?: {
    id: string;
    seq: number | null;
    status: string;
    output_dir: string;
    message: string;
  } | null;
  shots: {
    shot_index: number;
    bands: { filename: string; band: string; role: string; status: string; size_bytes: number }[];
  }[];
  rounds: {
    id: number;
    job_id: string | null;
    job_seq: number | null;
    job_status: string | null;
    output_dir: string;
    created_at: string | null;
    is_current: boolean;
  }[];
};

export type AccuracyClause = {
  no: string;
  text: string;
  result: string;
  detail: string;
};

export type AccuracyPoint = {
  name: string;
  col: number;
  row: number;
  east: number | null;
  north: number | null;
  lon: number | null;
  lat: number | null;
  gps_x: number | null;
  gps_y: number | null;
  gps_z: number | null;
  gps_col: number | null;
  gps_row: number | null;
  dist_m: number | null;
  err_px: number | null;
};

export type AccuracyState = {
  min_count: number;
  max_count: number;
  max_mean_px: number;
  crs: string;
  reference_band: string;
  ortho_epsg: string;
  gsd_m: number | null;
  evaluated: boolean;
  mean_px: number | null;
  max_px: number | null;
  points: AccuracyPoint[];
  bands: { id: string; label: string; reference: boolean; gsd_m: number | null }[];
  accepted: boolean;
  message: string;
  clauses: AccuracyClause[];
};

export type AccuracyView = {
  url: string;
  originCol: number;
  originRow: number;
  width: number;
  height: number;
  fullWidth: number;
  fullHeight: number;
};

export type JobBandFile = { id: string; label: string; name: string; bytes: number };

export type QualityView = {
  ready: boolean;
  has_pdf: boolean;
  message: string;
  project_name?: string;
  created_at?: string;
  crs_label?: string;
  n_scanned?: number | null;
  n_shots?: number | null;
  n_images?: number | null;
  n_registered?: number | null;
  n_filtered?: number | null;
  area_km2?: number | null;
  mean_agl_m?: number | null;
  elapsed_s?: number | null;
  at?: {
    rms_reprojection_px: number | null;
    n_points: number | null;
    n_observations: number | null;
    gps_rmse_m: number | null;
    gps_rmse_x_m: number | null;
    gps_rmse_y_m: number | null;
    gps_rmse_z_m: number | null;
    n_gps: number | null;
  };
  dsm?: {
    gsd: number | null;
    width: number | null;
    height: number | null;
    fill_ratio: number | null;
    z_min: number | null;
    z_median: number | null;
    z_max: number | null;
  };
  ortho?: { gsd: number | null; mode: string; blend: string };
  features?: { mean_keypoints: number | null; min_keypoints: number | null; max_keypoints: number | null; n_poor: number | null };
  matching?: { n_tracks: number | null; mean_tracks: number | null; n_matched_pairs: number | null };
  cameras?: { band: string; group?: number | null; n_images: number | null; n_registered: number | null; rms_px: number | null; width: number | null; height: number | null; f: number | null }[];
  histogram?: { label: string; error: number | null; dx: number | null; dy: number | null; dz: number | null }[];
  figures?: string[];
};

export const api = {
  login: async (username: string, password: string) => {
    const body = new URLSearchParams({ username, password });
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body,
    });
    if (!res.ok) throw new Error("登录失败");
    return res.json() as Promise<{ access_token: string; roles: string[] }>;
  },
  health: () => request<{ status: string }>("/api/health"),
  systemStatus: () =>
    request<{
      status: string;
      postgres: string;
      rabbitmq: string;
      worker: string;
      can_edit: boolean;
    }>("/api/system/status"),
  getWorkerEnv: () =>
    request<{
      worker_env: {
        rabbitmq_url: string;
        pythonpath: string;
        workdir: string;
        mplbackend: string;
        data_roots: string;
        default_input_dir: string;
        default_output_dir: string;
        source: string;
      };
      can_edit: boolean;
    }>("/api/system/worker-env"),
  updateWorkerEnv: (body: {
    data_roots: string;
    default_input_dir: string;
    default_output_dir: string;
  }) =>
    request<{
      worker_env: {
        rabbitmq_url: string;
        pythonpath: string;
        workdir: string;
        mplbackend: string;
        data_roots: string;
        default_input_dir: string;
        default_output_dir: string;
        source: string;
      };
      can_edit: boolean;
    }>("/api/system/worker-env", { method: "PUT", body: JSON.stringify(body) }),
  paramDefs: () => request<any[]>("/api/param-definitions"),
  profiles: () => request<any[]>("/api/profiles"),
  createProfile: (body: any) => request("/api/profiles", { method: "POST", body: JSON.stringify(body) }),
  updateProfile: (id: number, body: any) =>
    request(`/api/profiles/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  jobs: () => request<any[]>("/api/jobs"),
  job: (id: string) => request<any>(`/api/jobs/${id}`),
  createJob: (body: any) => request("/api/jobs", { method: "POST", body: JSON.stringify(body) }),
  nativeGsd: (inputDir: string) =>
    request<{
      native_gsd_m: number;
      n_samples: number;
      choices: { scale: number; ortho_gsd_m: number; dsm_gsd_m: number }[];
    }>(`/api/tools/native-gsd?input_dir=${encodeURIComponent(inputDir)}`),
  accuracyState: (id: string) => request<AccuracyState>(`/api/jobs/${id}/accuracy`),
  accuracyConfig: (
    id: string,
    body: { min_count: number; max_count: number; max_mean_px: number; reference_band?: string },
  ) => request<AccuracyState>(`/api/jobs/${id}/accuracy/config`, { method: "PUT", body: JSON.stringify(body) }),
  accuracyPick: (id: string, body: { col: number; row: number; name?: string }) =>
    request<AccuracyState>(`/api/jobs/${id}/accuracy/points`, { method: "POST", body: JSON.stringify(body) }),
  accuracyDeletePoint: (id: string, name: string) =>
    request<AccuracyState>(`/api/jobs/${id}/accuracy/points/${encodeURIComponent(name)}`, { method: "DELETE" }),
  accuracyGps: (id: string, name: string, body: { x: number | null; y: number | null; z: number | null; crs?: string }) =>
    request<AccuracyState>(`/api/jobs/${id}/accuracy/points/${encodeURIComponent(name)}/gps`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  accuracyEvaluate: (id: string) => request<AccuracyState>(`/api/jobs/${id}/accuracy/evaluate`, { method: "POST" }),
  accuracyWindow: async (id: string, band: string, cx?: number, cy?: number) => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const query = new URLSearchParams();
    if (band) query.set("band", band);
    if (cx != null) query.set("cx", String(cx));
    if (cy != null) query.set("cy", String(cy));
    const res = await fetch(`/api/jobs/${id}/accuracy/window?${query}`, { headers });
    if (res.status === 401) {
      redirectToLogin();
      return new Promise<AccuracyView>(() => {});
    }
    if (!res.ok) {
      const text = await res.text();
      let msg = text || res.statusText;
      try {
        const parsed = JSON.parse(text);
        if (typeof parsed.detail === "string") msg = parsed.detail;
      } catch {
        /* 保留原文 */
      }
      throw new Error(msg);
    }
    const blob = await res.blob();
    const num = (key: string) => Number(res.headers.get(key));
    return {
      url: URL.createObjectURL(blob),
      originCol: num("X-Origin-Col"),
      originRow: num("X-Origin-Row"),
      width: num("X-Win-Width"),
      height: num("X-Win-Height"),
      fullWidth: num("X-Full-Width"),
      fullHeight: num("X-Full-Height"),
    } satisfies AccuracyView;
  },
  accuracyBrowse: async (id: string, band: string) => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const query = new URLSearchParams();
    if (band) query.set("band", band);
    const res = await fetch(`/api/jobs/${id}/accuracy/browse?${query}`, { headers });
    if (res.status === 401) {
      redirectToLogin();
      return new Promise<AccuracyView>(() => {});
    }
    if (!res.ok) throw new Error("整幅正射打开失败");
    const blob = await res.blob();
    const num = (key: string) => Number(res.headers.get(key));
    return {
      url: URL.createObjectURL(blob),
      originCol: 0,
      originRow: 0,
      width: num("X-Win-Width"),
      height: num("X-Win-Height"),
      fullWidth: num("X-Full-Width"),
      fullHeight: num("X-Full-Height"),
    } satisfies AccuracyView;
  },
  accuracyOverview: async (id: string, band: string) => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const query = new URLSearchParams();
    if (band) query.set("band", band);
    const res = await fetch(`/api/jobs/${id}/accuracy/overview?${query}`, { headers });
    if (res.status === 401) {
      redirectToLogin();
      return new Promise<AccuracyView>(() => {});
    }
    if (!res.ok) throw new Error("整幅缩略图打开失败");
    const blob = await res.blob();
    const num = (key: string) => Number(res.headers.get(key));
    return {
      url: URL.createObjectURL(blob),
      originCol: 0,
      originRow: 0,
      width: num("X-Win-Width"),
      height: num("X-Win-Height"),
      fullWidth: num("X-Full-Width"),
      fullHeight: num("X-Full-Height"),
    } satisfies AccuracyView;
  },
  seamVersions: (id: string) =>
    request<{
      current: { id: string; label: string; created_at: string; note: string } | null;
      versions: {
        id: string;
        label: string;
        created_at: string;
        note: string;
        change?: { summary: string; places: { where: string; name: string; image_id: number }[] };
      }[];
    }>(`/api/jobs/${id}/seam-versions`),
  seamMap: (id: string, version?: string) =>
    request<{ width: number; height: number; features: { image_id: number; points: number[][] }[] }>(
      `/api/jobs/${id}/seam-map${version ? `?version=${encodeURIComponent(version)}` : ""}`,
    ),
  rgbPreview: async (id: string) => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const res = await fetch(`/api/jobs/${id}/rgb-preview.png`, { headers });
    if (res.status === 401) {
      redirectToLogin();
      return new Promise<Blob>(() => {});
    }
    if (!res.ok) throw new Error(readError(await res.text(), "RGB 拼图读取失败"));
    return res.blob();
  },
  seamPreview: async (id: string, version?: string) => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const query = version ? `?version=${encodeURIComponent(version)}` : "";
    const res = await fetch(`/api/jobs/${id}/seam-preview.png${query}`, { headers });
    if (res.status === 401) {
      redirectToLogin();
      return new Promise<Blob>(() => {});
    }
    if (!res.ok) throw new Error(readError(await res.text(), "正射预览失败"));
    return res.blob();
  },
  reseamJob: (id: string, body: { prefer_ids?: string; regions?: { image_id: number; points: number[][] }[]; clear_edits?: boolean }) =>
    request<any>(`/api/jobs/${id}/seam`, { method: "POST", body: JSON.stringify(body) }),
  cancelReseam: (id: string) => request<any>(`/api/jobs/${id}/seam-cancel`, { method: "POST" }),
  seamBranch: (id: string, version: string) =>
    request<any>(`/api/jobs/${id}/seam-branch`, { method: "POST", body: JSON.stringify({ version }) }),
  seamTasks: () => request<any[]>("/api/jobs/seam-tasks"),
  createSeamTask: (sourceJobId: string) =>
    request<any>("/api/jobs/seam-tasks", { method: "POST", body: JSON.stringify({ source_job_id: sourceJobId }) }),
  seamCandidates: (id: string, x: number, y: number) =>
    request<{ images: { image_id: number; name: string; center_offset: number }[] }>(
      `/api/jobs/${id}/seam-candidates?x=${encodeURIComponent(x)}&y=${encodeURIComponent(y)}`,
    ),
  seamCandidatesMany: (id: string, points: number[][]) =>
    request<{ images: { image_id: number; name: string; center_offset: number }[] }>(
      `/api/jobs/${id}/seam-candidates`,
      { method: "POST", body: JSON.stringify({ points }) },
    ),
  seamImage: async (id: string, imageId: number, maxSide?: number) => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const query = maxSide ? `?max_side=${encodeURIComponent(maxSide)}` : "";
    const res = await fetch(`/api/jobs/${id}/seam-image/${imageId}${query}`, { headers });
    if (res.status === 401) {
      redirectToLogin();
      return new Promise<Blob>(() => {});
    }
    if (!res.ok) throw new Error(readError(await res.text(), "原始影像读取失败"));
    return res.blob();
  },
  jobAction: (id: string, action: string, body?: any) =>
    request(`/api/jobs/${id}/${action}`, { method: "POST", body: JSON.stringify(body || {}) }),
  deleteJob: (id: string) => request<void>(`/api/jobs/${id}`, { method: "DELETE" }),
  clearJobs: () => request<{ deleted: number }>("/api/jobs", { method: "DELETE" }),
  logs: (id: string) => request<string>(`/api/jobs/${id}/logs?tail=800`),
  stageSteps: (id: string) => request<{ stages: Record<string, StageStep[]> }>(`/api/jobs/${id}/stage-steps`),
  openJobReport: (id: string) => openAuthedFile(`/api/jobs/${id}/report.pdf`, "质量报告.pdf"),
  jobReportUrl: async (id: string) => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const res = await fetch(`/api/jobs/${id}/report.pdf`, { headers });
    if (res.status === 401) {
      redirectToLogin();
      return "";
    }
    if (!res.ok) throw new Error(readError(await res.text(), "质量报告尚未生成"));
    const blob = await res.blob();
    const pdf = blob.type === "application/pdf" ? blob : new Blob([await blob.arrayBuffer()], { type: "application/pdf" });
    return URL.createObjectURL(pdf);
  },
  jobFiles: (id: string) =>
    request<{ bands: JobBandFile[]; originals: { name: string; label: string; bytes: number }[]; has_report: boolean }>(
      `/api/jobs/${id}/files`,
    ),
  downloadDeliverable: async (id: string, kind: "original" | "all" | "file", name?: string) => {
    const ticket = await request<{ ticket: string }>(`/api/jobs/${id}/files/ticket`, {
      method: "POST",
      body: JSON.stringify({ kind, name }),
    });
    const query = new URLSearchParams({ ticket: ticket.ticket });
    const a = document.createElement("a");
    a.href = `/api/jobs/${id}/files/download?${query}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  },
  quality: (id: string) => request<QualityView>(`/api/jobs/${id}/quality`),
  qualityFigure: async (id: string, name: string) => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const res = await fetch(`/api/jobs/${id}/quality/figures/${encodeURIComponent(name)}`, { headers });
    if (res.status === 401) {
      redirectToLogin();
      return "";
    }
    if (!res.ok) throw new Error("附图打开失败");
    return URL.createObjectURL(await res.blob());
  },
  workerInspect: () => request<any>("/api/system/worker"),
  inspectUpload: (file: File, onProgress?: (pct: number, phase: "upload" | "parse") => void) =>
    new Promise<InspectMeta>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/tools/inspect");
      const token = getToken();
      if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (ev) => {
        if (ev.lengthComputable) onProgress?.(Math.round((ev.loaded / ev.total) * 100), "upload");
      };
      xhr.upload.onload = () => onProgress?.(100, "parse");
      xhr.onload = () => {
        if (xhr.status === 401) {
          redirectToLogin();
          return;
        }
        if (xhr.status < 200 || xhr.status >= 300) {
          reject(new Error(readError(xhr.responseText, xhr.statusText || "上传失败")));
          return;
        }
        resolve(JSON.parse(xhr.responseText) as InspectMeta);
      };
      xhr.onerror = () => reject(new Error("上传失败"));
      const body = new FormData();
      body.append("file", file);
      xhr.send(body);
    }),
  inspectPreview: async (id: string) => {
    const headers = new Headers();
    const token = getToken();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const res = await fetch(`/api/tools/inspect/${id}/preview`, { headers });
    if (res.status === 401) {
      redirectToLogin();
      return new Promise<Blob>(() => {});
    }
    if (!res.ok) throw new Error(readError(await res.text(), "预览失败"));
    return res.blob();
  },
  inspectPixel: (id: string, col: number, row: number, signal?: AbortSignal) =>
    request<InspectPixel>(`/api/tools/inspect/${id}/pixel?col=${col}&row=${row}`, { signal }),
  killAllWorkerJobs: () => request<{ killed: number }>("/api/system/worker/kill-all", { method: "POST" }),
  cliGuide: () => request<string>("/api/docs/cli-guide"),
  paramGuide: () => request<string>("/api/docs/param-guide"),
  qualityGuide: () => request<string>("/api/docs/quality-guide"),
  users: () => request<any[]>("/api/users"),
  audits: () =>
    request<{ id: number; username: string | null; action: string; detail: Record<string, unknown>; created_at: string | null }[]>(
      "/api/audit",
    ),
  createUser: (body: { username: string; password: string; roles: string[] }) =>
    request("/api/users", { method: "POST", body: JSON.stringify(body) }),
  updateUser: (id: number, body: { password?: string; roles?: string[]; is_active?: boolean }) =>
    request(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  incrementalProjects: (page = 1, pageSize = 10) =>
    request<{ items: IncrementalProject[]; total: number; page: number; page_size: number }>(
      `/api/incremental/projects?page=${page}&page_size=${pageSize}`,
    ),
  incrementalCreate: (body: {
    name: string;
    input_dir: string;
    output_root: string;
    auto_run: boolean;
    scan_interval_minutes: number;
  }) =>
    request<IncrementalDetail>("/api/incremental/projects", { method: "POST", body: JSON.stringify(body) }),
  incrementalDetail: (id: string) => request<IncrementalDetail>(`/api/incremental/projects/${id}`),
  incrementalPatch: (id: string, body: { name?: string; auto_run?: boolean; scan_interval_minutes?: number }) =>
    request<IncrementalDetail>(`/api/incremental/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  incrementalDelete: (id: string) => request<void>(`/api/incremental/projects/${id}`, { method: "DELETE" }),
  incrementalScan: (id: string) => request<IncrementalDetail>(`/api/incremental/projects/${id}/scan`, { method: "POST" }),
  incrementalRun: (id: string) => request<IncrementalDetail>(`/api/incremental/projects/${id}/run`, { method: "POST" }),
  rbac: () => request<{ menus: Record<string, string[]> }>("/api/rbac"),
  updateRoleMenus: (role: string, menus: string[]) =>
    request<{ menus: Record<string, string[]> }>(`/api/rbac/roles/${role}`, {
      method: "PUT",
      body: JSON.stringify({ menus }),
    }),
  updatePermissionRoles: (key: string, roles: string[]) =>
    request<{ menus: Record<string, string[]> }>(`/api/rbac/permissions/${key}`, {
      method: "PUT",
      body: JSON.stringify({ roles }),
    }),
};
