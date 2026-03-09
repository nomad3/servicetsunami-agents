import api from './api';

const channelService = {
  enableWhatsApp: (data) => api.post('/channels/whatsapp/enable', data),
  disableWhatsApp: (data) => api.post('/channels/whatsapp/disable', data || {}),
  updateWhatsAppSettings: (data) => api.put('/channels/whatsapp/settings', data),
  getWhatsAppStatus: () => api.get('/channels/whatsapp/status'),
  startPairing: (data) => api.post('/channels/whatsapp/pair', data || {}),
  getPairingStatus: (params) => api.get('/channels/whatsapp/pair/status', { params }),
  logoutWhatsApp: (data) => api.post('/channels/whatsapp/logout', data || {}),
  sendWhatsApp: (data) => api.post('/channels/whatsapp/send', data),
};

export default channelService;
