const path = require('path');
const en = require(path.resolve(__dirname, '../i18n/locales/en/landing.json'));

function flatLookup(obj, key) {
  return key.split('.').reduce((o, k) => (o && o[k] !== undefined ? o[k] : null), obj);
}

const mockUseTranslation = () => ({
  t: (key, opts) => {
    const val = flatLookup(en, key);
    if (val === null) return key;
    if (typeof val === 'string' && opts) {
      return Object.entries(opts).reduce(
        (s, [k, v]) => s.replace(new RegExp(`\\{\\{${k}\\}\\}`, 'g'), v),
        val
      );
    }
    return typeof val === 'string' ? val : key;
  },
  i18n: { changeLanguage: jest.fn() },
});

module.exports = { mockUseTranslation };
