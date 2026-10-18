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
  evaluateJob: (id: string, n: number) =>
    request<{
      n_requested: number;
      n_used: number;
      threshold_px: number;
      rms_px: number;
      max_px: number;
      mean_px: number;
      n_over: number;
      within_tolerance: boolean;
      gsd_m: number;
      message: string;
    }>(`/api/jobs/${id}/evaluate`, { method: "POST", body: JSON.stringify({ n }) }),
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
