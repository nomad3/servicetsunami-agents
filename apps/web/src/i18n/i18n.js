import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import LanguageDetector from 'i18next-browser-languagedetector';

// English
import enCommon from './locales/en/common.json';
import enLanding from './locales/en/landing.json';
import enDatasets from './locales/en/datasets.json';
import enDashboard from './locales/en/dashboard.json';
import enChat from './locales/en/chat.json';
import enAgents from './locales/en/agents.json';
import enWorkflows from './locales/en/workflows.json';
import enMemory from './locales/en/memory.json';
import enIntegrations from './locales/en/integrations.json';
import enSettings from './locales/en/settings.json';
import enAuth from './locales/en/auth.json';
import enNotebooks from './locales/en/notebooks.json';
import enTools from './locales/en/tools.json';

// Spanish
import esCommon from './locales/es/common.json';
import esLanding from './locales/es/landing.json';
import esDatasets from './locales/es/datasets.json';
import esDashboard from './locales/es/dashboard.json';
import esChat from './locales/es/chat.json';
import esAgents from './locales/es/agents.json';
import esWorkflows from './locales/es/workflows.json';
import esMemory from './locales/es/memory.json';
import esIntegrations from './locales/es/integrations.json';
import esSettings from './locales/es/settings.json';
import esAuth from './locales/es/auth.json';
import esNotebooks from './locales/es/notebooks.json';
import esTools from './locales/es/tools.json';

const resources = {
  en: {
    common: enCommon,
    landing: enLanding,
    datasets: enDatasets,
    dashboard: enDashboard,
    chat: enChat,
    agents: enAgents,
    workflows: enWorkflows,
    memory: enMemory,
    integrations: enIntegrations,
    settings: enSettings,
    auth: enAuth,
    notebooks: enNotebooks,
    tools: enTools,
  },
  es: {
    common: esCommon,
    landing: esLanding,
    datasets: esDatasets,
    dashboard: esDashboard,
    chat: esChat,
    agents: esAgents,
    workflows: esWorkflows,
    memory: esMemory,
    integrations: esIntegrations,
    settings: esSettings,
    auth: esAuth,
    notebooks: esNotebooks,
    tools: esTools,
  },
};

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: 'en',
    supportedLngs: ['en', 'es'],
    ns: ['common', 'landing', 'datasets', 'dashboard', 'chat', 'agents', 'workflows', 'memory', 'integrations', 'settings', 'auth', 'notebooks', 'tools'],
    defaultNS: 'common',
    interpolation: {
      escapeValue: false,
    },
    detection: {
      order: ['localStorage', 'navigator', 'htmlTag'],
      caches: ['localStorage'],
      lookupLocalStorage: 'servicetsunami.lang',
    },
  });

export default i18n;
