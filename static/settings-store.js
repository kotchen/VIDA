(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.AIProfileStore = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  const VERSION = 2;
  const DEFAULT_TEMPERATURE = 0.1;

  function createId() {
    if (root && root.crypto && typeof root.crypto.randomUUID === 'function') {
      return root.crypto.randomUUID();
    }
    return `provider-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function cleanBaseUrl(value) {
    return String(value || '').trim().replace(/\/+$/, '');
  }

  function defaultTemperature(modelId) {
    return String(modelId || '').trim().toLowerCase() === 'k3' ? 1 : DEFAULT_TEMPERATURE;
  }

  function createProfile(name = 'Default') {
    return {
      id: createId(),
      name: String(name || '').trim() || 'Default',
      baseUrl: '',
      apiKey: '',
      lastModel: '',
      modelTemperatures: {},
    };
  }

  function normalizeProfile(value) {
    const source = value && typeof value === 'object' ? value : {};
    return {
      id: String(source.id || '').trim() || createId(),
      name: String(source.name || '').trim() || 'Default',
      baseUrl: cleanBaseUrl(source.baseUrl),
      apiKey: String(source.apiKey || ''),
      lastModel: String(source.lastModel || ''),
      modelTemperatures:
        source.modelTemperatures && typeof source.modelTemperatures === 'object'
          ? { ...source.modelTemperatures }
          : {},
    };
  }

  function blankSettings() {
    const profile = createProfile();
    return {
      version: VERSION,
      activeProfileId: profile.id,
      summaryLang: 'zh',
      profiles: [profile],
    };
  }

  function load(rawSettings) {
    try {
      if (!rawSettings) return blankSettings();
      const source = typeof rawSettings === 'string' ? JSON.parse(rawSettings) : rawSettings;

      if (source && source.version === VERSION && Array.isArray(source.profiles) && source.profiles.length) {
        const profiles = source.profiles.map(normalizeProfile);
        const requestedActive = String(source.activeProfileId || '');
        const activeProfileId = profiles.some((profile) => profile.id === requestedActive)
          ? requestedActive
          : profiles[0].id;
        return {
          version: VERSION,
          activeProfileId,
          summaryLang: String(source.summaryLang || 'zh'),
          profiles,
        };
      }

      if (source && typeof source === 'object') {
        const profile = createProfile('Default');
        profile.baseUrl = cleanBaseUrl(source.baseUrl);
        profile.apiKey = String(source.apiKey || '');
        profile.lastModel = String(source.model || '');
        if (profile.lastModel) {
          profile.modelTemperatures[profile.lastModel] = defaultTemperature(profile.lastModel);
        }
        return {
          version: VERSION,
          activeProfileId: profile.id,
          summaryLang: String(source.summaryLang || 'zh'),
          profiles: [profile],
        };
      }
    } catch (_) {
      return blankSettings();
    }

    return blankSettings();
  }

  function temperatureFor(profile, modelId) {
    const normalizedModel = String(modelId || '');
    const temperatures = profile && profile.modelTemperatures;
    const saved = temperatures && Number(temperatures[normalizedModel]);
    return Number.isFinite(saved) ? saved : defaultTemperature(normalizedModel);
  }

  function setModelTemperature(profile, modelId, value) {
    const normalizedModel = String(modelId || '');
    if (!normalizedModel) return normalizeProfile(profile);
    const updated = normalizeProfile(profile);
    const numericValue = Number(value);
    updated.modelTemperatures[normalizedModel] = Number.isFinite(numericValue)
      ? numericValue
      : defaultTemperature(normalizedModel);
    return updated;
  }

  function captureProfile(profile, fields) {
    const updated = normalizeProfile(profile);
    const source = fields && typeof fields === 'object' ? fields : {};
    updated.baseUrl = cleanBaseUrl(source.baseUrl);
    updated.apiKey = String(source.apiKey || '');
    updated.lastModel = String(source.modelId || '');
    if (updated.lastModel && !Object.prototype.hasOwnProperty.call(
      updated.modelTemperatures,
      updated.lastModel,
    )) {
      updated.modelTemperatures[updated.lastModel] = defaultTemperature(updated.lastModel);
    }
    return updated;
  }

  return {
    VERSION,
    DEFAULT_TEMPERATURE,
    createProfile,
    captureProfile,
    defaultTemperature,
    load,
    setModelTemperature,
    temperatureFor,
  };
});
