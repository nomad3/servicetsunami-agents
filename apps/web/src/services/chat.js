import api from '../utils/api';

const listSessions = () => api.get('/chat/sessions');

const createSession = (payload) => api.post('/chat/sessions', payload);

const listMessages = (sessionId) => api.get(`/chat/sessions/${sessionId}/messages`);

const postMessage = (sessionId, content) =>
  api.post(`/chat/sessions/${sessionId}/messages`, {
    content,
  });

const getSessionEntities = (sessionId) => api.get(`/chat/sessions/${sessionId}/entities`);

const postMessageWithFile = (sessionId, content, file) => {
  const formData = new FormData();
  formData.append('content', content || '');
  formData.append('file', file);
  return api.post(`/chat/sessions/${sessionId}/messages/upload`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  });
};

const chatService = {
  listSessions,
  createSession,
  listMessages,
  postMessage,
  postMessageWithFile,
  getSessionEntities,
};

export default chatService;
