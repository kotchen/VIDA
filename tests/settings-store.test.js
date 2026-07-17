const test = require('node:test');
const assert = require('node:assert/strict');
const Store = require('../static/settings-store.js');

test('migrates legacy settings into one Default provider profile', () => {
  const settings = Store.load(JSON.stringify({
    baseUrl: 'https://api.example/v1/',
    apiKey: 'secret',
    model: 'model-a',
    summaryLang: 'en',
  }));

  assert.equal(settings.version, 2);
  assert.equal(settings.summaryLang, 'en');
  assert.equal(settings.profiles.length, 1);
  assert.equal(settings.profiles[0].name, 'Default');
  assert.equal(settings.profiles[0].baseUrl, 'https://api.example/v1');
  assert.equal(settings.profiles[0].apiKey, 'secret');
  assert.equal(settings.profiles[0].lastModel, 'model-a');
  assert.equal(settings.profiles[0].modelTemperatures['model-a'], 0.1);
});

test('uses model-specific temperature defaults', () => {
  assert.equal(Store.defaultTemperature('k3'), 1);
  assert.equal(Store.defaultTemperature('K3'), 1);
  assert.equal(Store.defaultTemperature('deepseek-v4-flash'), 0.1);
});

test('falls back to a blank profile for invalid storage', () => {
  const settings = Store.load('{bad json');
  assert.equal(settings.version, 2);
  assert.equal(settings.profiles.length, 1);
  assert.equal(settings.profiles[0].name, 'Default');
  assert.equal(settings.profiles[0].baseUrl, '');
});

test('captures provider fields and the selected model', () => {
  const profile = Store.createProfile('Kimi');
  const updated = Store.captureProfile(profile, {
    baseUrl: 'https://api.kimi.com/coding/v1/',
    apiKey: 'kimi-secret',
    modelId: 'k3',
  });

  assert.equal(updated.name, 'Kimi');
  assert.equal(updated.baseUrl, 'https://api.kimi.com/coding/v1');
  assert.equal(updated.apiKey, 'kimi-secret');
  assert.equal(updated.lastModel, 'k3');
  assert.equal(updated.modelTemperatures.k3, 1);
});

test('remembers temperature independently for each model', () => {
  let profile = Store.createProfile('Provider');
  profile = Store.setModelTemperature(profile, 'k3', 1);
  profile = Store.setModelTemperature(profile, 'model-a', 0.4);

  assert.equal(Store.temperatureFor(profile, 'k3'), 1);
  assert.equal(Store.temperatureFor(profile, 'model-a'), 0.4);
  assert.equal(Store.temperatureFor(profile, 'new-model'), 0.1);
});
