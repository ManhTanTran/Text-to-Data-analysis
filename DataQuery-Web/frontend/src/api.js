import axios from "axios";

const api = axios.create({ baseURL: "/api" });

export const getStatus = () => api.get("/status").then((r) => r.data);
export const uploadFile = (file) => {
  const form = new FormData();
  form.append("file", file);
  return api.post("/upload", form).then((r) => r.data);
};
export const getSuggestions = (sessionId, language = "vi") =>
  api.get(`/suggest/${sessionId}`, { params: { language } }).then((r) => r.data.suggestions);
export const runQuery = (sessionId, question, language = "vi") =>
  api.post("/query", { session_id: sessionId, question, language }).then((r) => r.data);
