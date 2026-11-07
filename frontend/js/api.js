// Thin client for the CQA backend. All calls go through our server (organisers' API has no CORS).
const BASE = ((window.CQA_CONFIG && window.CQA_CONFIG.apiBase) || "").replace(/\/$/, "");

async function request(method, path, body) {
  const init = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const res = await fetch(BASE + path, init);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const data = await res.json();
      if (typeof data.detail === "string") msg = data.detail;
      else if (Array.isArray(data.detail)) msg = data.detail.map((d) => `${(d.loc || []).slice(1).join(".")}: ${d.msg}`).join("; ");
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

const qs = (params) => {
  const p = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => v !== undefined && v !== null && v !== "" && p.set(k, v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const api = {
  health: () => request("GET", "/api/health"),
  stats: () => request("GET", "/api/stats"),

  conversations: () => request("GET", "/api/conversations"),
  conversation: (id) => request("GET", `/api/conversations/${encodeURIComponent(id)}`),
  importGroup: (groupId) => request("POST", "/api/import", { group_id: groupId }),
  upload: (file, group) => {
    const fd = new FormData();
    fd.append("file", file);
    if (group) fd.append("group", group);
    return request("POST", "/api/upload", fd);
  },
  audioUrl: (id) => `${BASE}/api/conversations/${encodeURIComponent(id)}/audio`,

  evaluate: (body) => request("POST", "/api/evaluations", body),
  batch: (id) => request("GET", `/api/batches/${id}`),
  evaluation: (id) => request("GET", `/api/evaluations/${id}`),

  createAppeal: (evaluationId, body) => request("POST", `/api/evaluations/${evaluationId}/appeals`, body),
  appeals: (status) => request("GET", `/api/appeals${qs({ status })}`),
  appeal: (id) => request("GET", `/api/appeals/${id}`),
  decide: (id, body) => request("POST", `/api/appeals/${id}/decision`, body),

  checklists: () => request("GET", "/api/checklists"),
  checklist: (id) => request("GET", `/api/checklists/${id}`),
  activeChecklist: () => request("GET", "/api/checklists/active"),
  saveChecklist: (body) => request("POST", "/api/checklists", body),
  activateChecklist: (id) => request("POST", `/api/checklists/${id}/activate`),
};
