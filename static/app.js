/* ────────────────────────────────────────────────────────────
   AI Video Transcriber · app.js
   ──────────────────────────────────────────────────────────── */

class VideoTranscriber {
  constructor() {
    this.currentTaskId  = null;
    this.eventSource    = null;
    this.apiBase        = '/api';
    this.currentLang    = 'en';

    /* Smart progress simulation */
    this.sp = {
      enabled: false, current: 0, target: 15,
      lastServer: 0, interval: null, startTime: null, stage: 'preparing'
    };

    this.i18n = {
      en: {
        title:                   'AI Video Transcriber',
        subtitle:                'Supports automatic transcription and AI summary for 30+ platforms',
        video_url_placeholder:   'Paste YouTube, Tiktok, Bilibili or other platform video URLs...',
        start_transcription:     'Transcribe',
        ai_settings:             'AI Settings',
        model_base_url:          'Model API Base URL',
        model_base_url_placeholder: 'https://openrouter.ai/api/v1',
        api_key:                 'API Key',
        api_key_placeholder:     'sk-...',
        fetch_models:            'Fetch',
        model_select:            'Model',
        model_default:           '— use server default —',
        provider_profile:        'Provider profile',
        profile_name:            'Profile name',
        temperature:             'Temperature',
        profile_saved:           'Profile saved',
        profile_delete_confirm:  'Delete this provider profile?',
        summary_language:        'Summary Language',
        processing_progress:     'Processing',
        preparing:               'Preparing…',
        transcript_text:         'Transcript',
        intelligent_summary:     'AI Summary',
        translation:             'Translation',
        download_transcript:     'Transcript',
        download_translation:    'Translation',
        download_summary:        'Summary',
        empty_hint:              'Paste a video URL or drop a file above and let AI do the heavy lifting.',
        footer_text:             'This tool is part of <a href="https://sipsip.ai" target="_blank" style="color:var(--accent-text);text-decoration:none;">sipsip.ai</a> — distill anything and get daily AI briefs from your favorite creators',
        processing:              'Processing…',
        downloading_video:       'Downloading audio…',
        parsing_video:           'Parsing video info…',
        transcribing_audio:      'Transcribing audio…',
        optimizing_transcript:   'Optimizing transcript…',
        generating_summary:      'Generating summary…',
        detecting_subtitles:     'Detecting subtitles…',
        subtitle_found:          'Subtitles found! Processing text…',
        no_subtitle:             'No subtitles found, downloading audio…',
        mode_subtitle:           '⚡ Subtitle',
        mode_whisper:            '🎙 Whisper',
        completed:               'Done!',
        error_invalid_url:       'Please enter a valid video URL',
        error_processing_failed: 'Processing failed: ',
        error_no_download:       'No file available for download',
        error_download_failed:   'Download failed: ',
        fetching_models:         'Fetching models…',
        models_loaded:           (n) => `${n} models loaded`,
        models_error:            'Failed to fetch models',
        upload_or:               'or drop your files',
        upload_formats:          '.mp3 · .mp4 · .wav · .m4a · .webm · .mkv · .ogg · .flac',
        upload_files_btn:        'Upload files',
        error_upload_type:       'Unsupported file type',
        error_upload_empty:      'File is empty',
        error_upload_size:       (mb) => `File exceeds ${mb} MB limit`,
        library_title:           'Generated Files',
        library_empty:           'No generated files yet — run a transcription first.',
        library_refresh:         'Refresh',
        library_download:        'Download',
        library_close:           'Close',
        library_delete:          'Delete',
        library_ungrouped:       'Ungrouped (legacy)',
        library_files_count:     (n) => `${n} file${n > 1 ? 's' : ''}`,
        library_delete_file_confirm:  'Delete this file?',
        library_delete_group_confirm: 'Delete this folder and all its files?',
        kind_raw:                'Raw',
        kind_transcript:         'Transcript',
        kind_translation:        'Translation',
        kind_summary:            'Summary',
        kind_other:              'File',
        stage_detect:            'Detect subtitles',
        stage_subtitle:          'Fetch subtitles',
        stage_download:          'Download audio',
        stage_transcribe:        'Transcribe audio',
        stage_optimize:          'Optimize text',
        stage_translate:         'Translate text',
        stage_summary:           'Generate summary',
        fetching_subtitles:      'Fetching subtitles…',
        generating_translation:  'Generating translation…',
        dag_done:                'Done',
      },
      zh: {
        title:                   'AI 视频转录器',
        subtitle:                '粘贴 YouTube、TikTok 或任意公开视频链接，获取转录文本和 AI 摘要。',
        video_url_placeholder:   '请输入视频链接…',
        start_transcription:     '开始转录',
        ai_settings:             'AI 设置',
        model_base_url:          'Model API 地址',
        model_base_url_placeholder: 'https://openrouter.ai/api/v1',
        api_key:                 'API Key',
        api_key_placeholder:     'sk-...',
        fetch_models:            '获取',
        model_select:            '模型',
        model_default:           '— 使用服务器默认 —',
        provider_profile:        '供应商配置',
        profile_name:            '配置名称',
        temperature:             'Temperature',
        profile_saved:           '配置已保存',
        profile_delete_confirm:  '确定删除当前供应商配置吗？',
        summary_language:        '摘要语言',
        processing_progress:     '处理进度',
        preparing:               '准备中…',
        transcript_text:         '转录文本',
        intelligent_summary:     '智能摘要',
        translation:             '翻译',
        download_transcript:     '转录',
        download_translation:    '翻译',
        download_summary:        '摘要',
        empty_hint:              '在上方粘贴视频链接或拖放文件，让 AI 来处理一切。',
        footer_text:             '本工具是 <a href="https://sipsip.ai" target="_blank" style="color:var(--accent-text);text-decoration:none;">sipsip.ai</a> 的一部分 — 提取任何内容要点并构建你自己的知识库。',
        processing:              '处理中…',
        downloading_video:       '正在下载音频…',
        parsing_video:           '正在解析视频信息…',
        transcribing_audio:      '正在转录音频…',
        optimizing_transcript:   '正在优化转录文本…',
        generating_summary:      '正在生成摘要…',
        detecting_subtitles:     '正在检测字幕…',
        subtitle_found:          '字幕获取成功！正在处理文本…',
        no_subtitle:             '未找到字幕，正在下载音频…',
        mode_subtitle:           '⚡ 字幕模式',
        mode_whisper:            '🎙 Whisper 模式',
        completed:               '处理完成！',
        error_invalid_url:       '请输入有效的视频链接',
        error_processing_failed: '处理失败：',
        error_no_download:       '没有可下载的文件',
        error_download_failed:   '下载失败：',
        fetching_models:         '正在获取模型列表…',
        models_loaded:           (n) => `已加载 ${n} 个模型`,
        models_error:            '获取模型失败',
        upload_or:               '或拖放文件到此处',
        upload_formats:          '.mp3 · .mp4 · .wav · .m4a · .webm · .mkv · .ogg · .flac',
        upload_files_btn:        '上传文件',
        error_upload_type:       '不支持的文件类型',
        error_upload_empty:      '文件为空',
        error_upload_size:       (mb) => `文件超过 ${mb} MB 限制`,
        library_title:           '生成文件',
        library_empty:           '还没有生成文件 — 先运行一次转录吧。',
        library_refresh:         '刷新',
        library_download:        '下载',
        library_close:           '关闭',
        library_delete:          '删除',
        library_ungrouped:       '未分组（旧版）',
        library_files_count:     (n) => `${n} 个文件`,
        library_delete_file_confirm:  '确定删除这个文件吗？',
        library_delete_group_confirm: '确定删除整个文件夹及其所有文件吗？',
        kind_raw:                '原始',
        kind_transcript:         '转录',
        kind_translation:        '翻译',
        kind_summary:            '摘要',
        kind_other:              '文件',
        stage_detect:            '检测字幕',
        stage_subtitle:          '获取字幕',
        stage_download:          '下载音频',
        stage_transcribe:        '转录音频',
        stage_optimize:          '优化文本',
        stage_translate:         '翻译文本',
        stage_summary:           '生成摘要',
        fetching_subtitles:      '正在获取字幕…',
        generating_translation:  '正在生成翻译…',
        dag_done:                '完成',
      }
    };

    this._initElements();
    this._bindEvents();
    this._loadSettings();
    this._initLibrary();
    this._switchLang('en');
  }

  /* ── Elements ─────────────────────────────────────────── */
  _initElements() {
    this.form               = document.getElementById('videoForm');
    this.videoUrlInput      = document.getElementById('videoUrl');
    this.submitBtn          = document.getElementById('submitBtn');
    this.summaryLangSel     = document.getElementById('summaryLanguage');
    this.langToggle         = document.getElementById('langToggle');
    this.langText           = document.getElementById('langText');
    this.errorBanner        = document.getElementById('errorBanner');
    this.errorMsg           = document.getElementById('errorMsg');
    this.emptyState         = document.getElementById('emptyState');
    this.progressPanel      = document.getElementById('progressPanel');
    this.modeBadge          = document.getElementById('modeBadge');
    this.progressStatus     = document.getElementById('progressStatus');
    this.progressFill       = document.getElementById('progressFill');
    this.dagList            = document.getElementById('dagList');
    this.resultsPanel       = document.getElementById('resultsPanel');
    this.scriptContent      = document.getElementById('scriptContent');
    this.summaryContent     = document.getElementById('summaryContent');
    this.translationContent = document.getElementById('translationContent');
    this.dlScript           = document.getElementById('downloadScript');
    this.dlTranslation      = document.getElementById('downloadTranslation');
    this.dlSummary          = document.getElementById('downloadSummary');
    this.translationTabBtn  = document.getElementById('translationTabBtn');
    this.tabBtns            = document.querySelectorAll('.tab-btn');
    this.tabPanes           = document.querySelectorAll('.tab-pane');
    // settings
    this.settingsToggle     = document.getElementById('settingsToggle');
    this.settingsBody       = document.getElementById('settingsBody');
    this.modelBaseUrl       = document.getElementById('modelBaseUrl');
    this.apiKeyInput        = document.getElementById('apiKeyInput');
    this.providerProfileSelect = document.getElementById('providerProfileSelect');
    this.addProfileBtn      = document.getElementById('addProfileBtn');
    this.saveProfileBtn     = document.getElementById('saveProfileBtn');
    this.deleteProfileBtn   = document.getElementById('deleteProfileBtn');
    this.profileNameRow     = document.getElementById('profileNameRow');
    this.profileNameInput   = document.getElementById('profileNameInput');
    this.fetchModelsBtn     = document.getElementById('fetchModelsBtn');
    this.fetchStatus        = document.getElementById('fetchStatus');
    this.modelSelect        = document.getElementById('modelSelect');
    this.temperatureInput   = document.getElementById('temperatureInput');
    this.fetchIcon          = document.getElementById('fetchIcon');
    this.uploadZone         = document.getElementById('uploadZone');
    this.uploadPickBtn      = document.getElementById('uploadPickBtn');
    this.fileInput          = document.getElementById('fileInput');
    this.uploadMaxMb        = 200;
    this._allowedUploadExts = new Set(['.txt', '.mp3', '.mp4', '.m4a', '.wav', '.webm', '.mkv', '.ogg', '.flac']);
    // library
    this.libraryCard        = document.getElementById('libraryCard');
    this.libraryHeader      = document.getElementById('libraryHeader');
    this.libraryGroupsEl    = document.getElementById('libraryGroups');
    this.libraryEmpty       = document.getElementById('libraryEmpty');
    this.libraryCount       = document.getElementById('libraryCount');
    this.libraryRefreshBtn  = document.getElementById('libraryRefreshBtn');
    this.libModalBackdrop   = document.getElementById('libModalBackdrop');
    this.libModalBadge      = document.getElementById('libModalBadge');
    this.libModalTitle      = document.getElementById('libModalTitle');
    this.libModalBody       = document.getElementById('libModalBody');
    this.libModalDownload   = document.getElementById('libModalDownload');
    this.libModalClose      = document.getElementById('libModalClose');
    this._libOpenGroups     = new Set();
    this._libModalFile      = null;
  }

  /* ── Events ───────────────────────────────────────────── */
  _bindEvents() {
    this.form.addEventListener('submit', (e) => { e.preventDefault(); this._startTranscription(); });

    this.langToggle.addEventListener('click', () => {
      this._switchLang(this.currentLang === 'en' ? 'zh' : 'en');
    });

    // Settings toggle
    this.settingsToggle.addEventListener('click', () => {
      const open = this.settingsBody.classList.toggle('open');
      this.settingsToggle.classList.toggle('open', open);
    });

    // Fetch models
    this.fetchModelsBtn.addEventListener('click', () => this._fetchModels());
    this.addProfileBtn.addEventListener('click', () => this._beginAddProfile());
    this.saveProfileBtn.addEventListener('click', () => this._saveActiveProfile());
    this.deleteProfileBtn.addEventListener('click', () => this._deleteActiveProfile());
    this.providerProfileSelect.addEventListener('change', () => {
      this._captureActiveProfile();
      this._applyProfile(this.providerProfileSelect.value);
    });

    // Auto-fetch when both fields filled (debounced)
    const debouncedFetch = this._debounce(() => {
      if (this.modelBaseUrl.value.trim() && this.apiKeyInput.value.trim()) this._fetchModels();
    }, 900);
    this.modelBaseUrl.addEventListener('input', debouncedFetch);
    this.apiKeyInput.addEventListener('input', debouncedFetch);

    // Persist settings
    [this.modelBaseUrl, this.apiKeyInput, this.summaryLangSel].forEach(el => {
      el.addEventListener('change', () => this._saveSettings());
    });
    this.modelSelect.addEventListener('change', () => {
      this._restoreModelTemperature();
      this._saveSettings();
    });
    this.temperatureInput.addEventListener('change', () => this._saveSettings());

    // Tabs
    this.tabBtns.forEach(btn => {
      btn.addEventListener('click', () => this._switchTab(btn.dataset.tab));
    });

    // Downloads
    this.dlScript.addEventListener('click',      () => this._downloadFile('script'));
    this.dlTranslation.addEventListener('click', () => this._downloadFile('translation'));
    this.dlSummary.addEventListener('click',     () => this._downloadFile('summary'));

    // Library
    if (this.libraryHeader) {
      this.libraryHeader.addEventListener('click', (e) => {
        if (this.libraryRefreshBtn.contains(e.target)) return;
        this._toggleLibrary();
      });
      this.libraryHeader.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this._toggleLibrary(); }
      });
      this.libraryRefreshBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this._loadLibrary();
      });
      this.libModalClose.addEventListener('click', () => this._closeLibModal());
      this.libModalBackdrop.addEventListener('click', (e) => {
        if (e.target === this.libModalBackdrop) this._closeLibModal();
      });
      this.libModalDownload.addEventListener('click', () => {
        if (this._libModalFile) this._downloadLibFile(this._libModalFile.folder, this._libModalFile.name);
      });
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.libModalBackdrop.classList.contains('show')) this._closeLibModal();
      });
    }

    if (this.uploadPickBtn && this.fileInput && this.uploadZone) {
      this.uploadPickBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        this.fileInput.click();
      });
      this.uploadZone.addEventListener('click', (e) => {
        if (e.target === this.uploadPickBtn || this.uploadPickBtn.contains(e.target)) return;
        this.fileInput.click();
      });
      this.fileInput.addEventListener('change', () => {
        const f = this.fileInput.files && this.fileInput.files[0];
        this.fileInput.value = '';
        if (f) this._startFileUpload(f);
      });
      ['dragenter', 'dragover'].forEach((ev) => {
        this.uploadZone.addEventListener(ev, (e) => {
          e.preventDefault();
          e.stopPropagation();
          this.uploadZone.classList.add('dragover');
        });
      });
      this.uploadZone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        if (!this.uploadZone.contains(e.relatedTarget)) {
          this.uploadZone.classList.remove('dragover');
        }
      });
      this.uploadZone.addEventListener('drop', (e) => {
        e.preventDefault();
        e.stopPropagation();
        this.uploadZone.classList.remove('dragover');
        const f = e.dataTransfer.files && e.dataTransfer.files[0];
        if (f) this._startFileUpload(f);
      });
    }
  }

  /* ── i18n ─────────────────────────────────────────────── */
  t(key) { return this.i18n[this.currentLang][key] || this.i18n['en'][key] || key; }

  _switchLang(lang) {
    this.currentLang = lang;
    this.langText.textContent = lang === 'en' ? 'English' : '中文';
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
    document.title = this.t('title');

    document.querySelectorAll('[data-i18n]').forEach(el => {
      const v = this.t(el.dataset.i18n);
      if (typeof v === 'string') {
        // footer 等允许含 HTML 的 key 用 innerHTML，其余保持 textContent
        if (el.dataset.i18n === 'footer_text') el.innerHTML = v;
        else el.textContent = v;
      }
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
      const v = this.t(el.dataset.i18nPlaceholder);
      if (typeof v === 'string') el.placeholder = v;
    });
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
      const v = this.t(el.dataset.i18nTitle);
      if (typeof v === 'string') el.title = v;
    });
  }

  /* ── Settings persistence ─────────────────────────────── */
  _getActiveProfile() {
    return this.settings?.profiles.find(profile => profile.id === this.settings.activeProfileId) || null;
  }

  _writeSettings() {
    try { localStorage.setItem('vt_settings', JSON.stringify(this.settings)); } catch (_) {}
  }

  _captureActiveProfile() {
    const profile = this._getActiveProfile();
    if (!profile) return;
    let updated = window.AIProfileStore.captureProfile(profile, {
      baseUrl: this.modelBaseUrl.value,
      apiKey: this.apiKeyInput.value,
      modelId: this.modelSelect.value,
    });
    if (updated.lastModel) {
      updated = window.AIProfileStore.setModelTemperature(
        updated,
        updated.lastModel,
        this.temperatureInput.value,
      );
    }
    const index = this.settings.profiles.findIndex(item => item.id === profile.id);
    this.settings.profiles[index] = updated;
    this.settings.summaryLang = this.summaryLangSel.value;
  }

  _renderProfileOptions() {
    this.providerProfileSelect.innerHTML = '';
    this.settings.profiles.forEach(profile => {
      const option = document.createElement('option');
      option.value = profile.id;
      option.textContent = profile.name;
      this.providerProfileSelect.appendChild(option);
    });
    this.providerProfileSelect.value = this.settings.activeProfileId;
  }

  _applyProfile(profileId, { fetchModels = true } = {}) {
    const profile = this.settings.profiles.find(item => item.id === profileId) || this.settings.profiles[0];
    if (!profile) return;
    this._modelFetchToken = (this._modelFetchToken || 0) + 1;
    this.settings.activeProfileId = profile.id;
    this._renderProfileOptions();
    this.modelBaseUrl.value = profile.baseUrl;
    this.apiKeyInput.value = profile.apiKey;
    this.modelSelect.innerHTML = `<option value="">${this.t('model_default')}</option>`;
    this.temperatureInput.value = window.AIProfileStore.temperatureFor(profile, profile.lastModel);
    this._setFetchStatus('', '');
    this._writeSettings();

    if (profile.baseUrl || profile.apiKey) {
      this.settingsBody.classList.add('open');
      this.settingsToggle.classList.add('open');
    }
    if (fetchModels && profile.baseUrl && profile.apiKey) {
      setTimeout(() => this._fetchModels(true), 0);
    }
  }

  _beginAddProfile() {
    this._captureActiveProfile();
    const profile = window.AIProfileStore.createProfile('New provider');
    this.settings.profiles.push(profile);
    this.settings.activeProfileId = profile.id;
    this._applyProfile(profile.id, { fetchModels: false });
    this.profileNameInput.value = '';
    this.profileNameRow.hidden = false;
    this.profileNameInput.focus();
  }

  _saveActiveProfile() {
    this._captureActiveProfile();
    const profile = this._getActiveProfile();
    const enteredName = this.profileNameInput.value.trim();
    if (profile && enteredName) profile.name = enteredName;
    this.profileNameRow.hidden = true;
    this.profileNameInput.value = '';
    this._renderProfileOptions();
    this._writeSettings();
    this._setFetchStatus('ok', this.t('profile_saved'));
  }

  _deleteActiveProfile() {
    if (!window.confirm(this.t('profile_delete_confirm'))) return;
    const activeId = this.settings.activeProfileId;
    this.settings.profiles = this.settings.profiles.filter(profile => profile.id !== activeId);
    if (!this.settings.profiles.length) {
      this.settings.profiles.push(window.AIProfileStore.createProfile('Default'));
    }
    this.profileNameRow.hidden = true;
    this._applyProfile(this.settings.profiles[0].id);
  }

  _restoreModelTemperature() {
    const profile = this._getActiveProfile();
    this.temperatureInput.value = window.AIProfileStore.temperatureFor(
      profile,
      this.modelSelect.value,
    );
  }

  _saveSettings() {
    this._captureActiveProfile();
    this._writeSettings();
  }

  _loadSettings() {
    const raw = localStorage.getItem('vt_settings');
    this.settings = window.AIProfileStore.load(raw);
    this.summaryLangSel.value = this.settings.summaryLang;
    this._renderProfileOptions();
    this._applyProfile(this.settings.activeProfileId);
    this._writeSettings();
  }

  /* ── Fetch models ─────────────────────────────────────── */
  async _fetchModels(silent = false) {
    const fetchToken = ++this._modelFetchToken;
    const profileId = this.settings.activeProfileId;
    const baseUrl = this.modelBaseUrl.value.trim().replace(/\/$/, '');
    const apiKey  = this.apiKeyInput.value.trim();

    if (!baseUrl || !apiKey) {
      if (!silent) this._setFetchStatus('err', this.t('api_key') + ' & URL required');
      return;
    }

    this.fetchModelsBtn.disabled = true;
    this.fetchIcon.classList.add('spinning');
    if (!silent) this._setFetchStatus('', this.t('fetching_models'));

    try {
      const fd = new FormData();
      fd.append('base_url', baseUrl);
      fd.append('api_key',  apiKey);

      const resp = await fetch(`${this.apiBase}/models`, { method: 'POST', body: fd });
      if (fetchToken !== this._modelFetchToken || profileId !== this.settings.activeProfileId) return;
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      const models = data.data || data.models || [];
      const savedModel = this._getActiveProfile()?.lastModel || '';

      // Rebuild select options
      this.modelSelect.innerHTML = `<option value="">${this.t('model_default')}</option>`;
      models.forEach(m => {
        const opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.name || m.id;
        this.modelSelect.appendChild(opt);
      });

      // Restore previously selected model
      if (savedModel) {
        if (![...this.modelSelect.options].some(option => option.value === savedModel)) {
          const savedOption = document.createElement('option');
          savedOption.value = savedModel;
          savedOption.textContent = savedModel;
          this.modelSelect.appendChild(savedOption);
        }
        this.modelSelect.value = savedModel;
      }
      this._restoreModelTemperature();
      this._saveSettings();

      this._setFetchStatus('ok', typeof this.t('models_loaded') === 'function'
        ? this.t('models_loaded')(models.length)
        : `${models.length} models`);

    } catch (e) {
      if (fetchToken !== this._modelFetchToken || profileId !== this.settings.activeProfileId) return;
      console.warn('Model fetch error:', e);
      this._setFetchStatus('err', this.t('models_error') + ': ' + e.message);
    } finally {
      if (fetchToken === this._modelFetchToken) {
        this.fetchModelsBtn.disabled = false;
        this.fetchIcon.classList.remove('spinning');
      }
    }
  }

  _setFetchStatus(cls, msg) {
    this.fetchStatus.className = 'fetch-status' + (cls ? ` ${cls}` : '');
    this.fetchStatus.textContent = msg;
  }

  /* ── Transcription ────────────────────────────────────── */
  async _startTranscription() {
    if (this.submitBtn.disabled) return;

    const url     = this.videoUrlInput.value.trim();
    const sumLang = this.summaryLangSel.value;

    if (!url) { this._showError(this.t('error_invalid_url')); return; }

    this._setLoading(true);
    this._hideError();
    this._showProgress();

    try {
      const fd = new FormData();
      fd.append('url',              url);
      fd.append('summary_language', sumLang);

      const apiKey  = this.apiKeyInput.value.trim();
      const baseUrl = this.modelBaseUrl.value.trim().replace(/\/$/, '');
      const modelId = this.modelSelect.value;
      if (apiKey)  fd.append('api_key',       apiKey);
      if (baseUrl) fd.append('model_base_url', baseUrl);
      if (modelId) fd.append('model_id',       modelId);
      fd.append('temperature', this.temperatureInput.value || '0.1');

      const resp = await fetch(`${this.apiBase}/process-video`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail || 'Request failed');
      }

      const data = await resp.json();
      this.currentTaskId = data.task_id;

      this._initSP();
      this._updateProgress(5, this.t('preparing'), true);
      this._startSSE();
      this._saveSettings();

    } catch (err) {
      this._showError(this.t('error_processing_failed') + err.message);
      this._setLoading(false);
      this._hideProgress();
    }
  }

  async _startFileUpload(file) {
    if (this.submitBtn.disabled) return;

    const parts = (file.name || '').split('.');
    const ext = parts.length > 1 ? ('.' + parts.pop().toLowerCase()) : '';
    if (!this._allowedUploadExts.has(ext)) {
      this._showError(this.t('error_upload_type'));
      return;
    }
    if (!file.size) {
      this._showError(this.t('error_upload_empty'));
      return;
    }
    const maxB = this.uploadMaxMb * 1024 * 1024;
    if (file.size > maxB) {
      this._showError(this.t('error_upload_size')(this.uploadMaxMb));
      return;
    }

    this._setLoading(true);
    this._hideError();
    this._showProgress();

    const sumLang = this.summaryLangSel.value;
    try {
      const fd = new FormData();
      fd.append('file', file, file.name);
      fd.append('summary_language', sumLang);

      const apiKey  = this.apiKeyInput.value.trim();
      const baseUrl = this.modelBaseUrl.value.trim().replace(/\/$/, '');
      const modelId = this.modelSelect.value;
      if (apiKey)  fd.append('api_key',       apiKey);
      if (baseUrl) fd.append('model_base_url', baseUrl);
      if (modelId) fd.append('model_id',       modelId);
      fd.append('temperature', this.temperatureInput.value || '0.1');

      const resp = await fetch(`${this.apiBase}/process-video`, { method: 'POST', body: fd });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        const d = err.detail;
        const msg = typeof d === 'string'
          ? d
          : (Array.isArray(d) && d[0] && (d[0].msg || d[0].message))
            || `HTTP ${resp.status}`;
        throw new Error(msg);
      }

      const data = await resp.json();
      this.currentTaskId = data.task_id;

      this._initSP();
      this._updateProgress(5, this.t('preparing'), true);
      this._startSSE();
      this._saveSettings();

    } catch (err) {
      this._showError(this.t('error_processing_failed') + err.message);
      this._setLoading(false);
      this._hideProgress();
    }
  }

  /* ── SSE ──────────────────────────────────────────────── */
  _startSSE() {
    if (!this.currentTaskId) return;
    this.eventSource = new EventSource(`${this.apiBase}/task-stream/${this.currentTaskId}`);

    this.eventSource.onmessage = (ev) => {
      try {
        const task = JSON.parse(ev.data);
        if (task.type === 'heartbeat') return;

        this._updateProgress(task.progress, task.message, true);

        if (task.status === 'completed') {
          this._stopSP(); this._stopSSE(); this._setLoading(false); this._hideProgress();
          this._showResults(task.script, task.summary, task.video_title, task.translation, task.detected_language, task.summary_language);
          this._loadLibrary();
        } else if (task.status === 'error') {
          this._stopSP(); this._stopSSE(); this._setLoading(false); this._hideProgress();
          this._showError(task.error || 'Processing error');
        }
      } catch (_) {}
    };

    this.eventSource.onerror = async () => {
      this._stopSSE();
      try {
        if (this.currentTaskId) {
          const r = await fetch(`${this.apiBase}/task-status/${this.currentTaskId}`);
          if (r.ok) {
            const task = await r.json();
            if (task?.status === 'completed') {
              this._stopSP(); this._setLoading(false); this._hideProgress();
              this._showResults(task.script, task.summary, task.video_title, task.translation, task.detected_language, task.summary_language);
              this._loadLibrary();
              return;
            }
          }
        }
      } catch (_) {}
      this._showError(this.t('error_processing_failed') + 'SSE disconnected');
      this._setLoading(false);
    };
  }

  _stopSSE() {
    if (this.eventSource) { this.eventSource.close(); this.eventSource = null; }
  }

  /* ── Progress ─────────────────────────────────────────── */
  _updateProgress(pct, msg, fromServer = false) {
    if (fromServer) {
      this._stopSP();
      this.sp.lastServer = pct;
      this.sp.current    = pct;
      this._renderProgress(pct, msg);
      this._updateStage(pct, msg);
      this._startSP();
    } else {
      this._renderProgress(pct, msg);
    }
  }

  _updateStage(pct, msg) {
    const m = (msg || '').toLowerCase();

    // ── 字幕路径（快速）──────────────────────────────────────
    if (m.includes('获取成功') || m.includes('subtitle found') || m.includes('字幕获取')) {
      this.sp.stage = 'subtitle_found';
      this.sp.target = 55;
      this._setModeBadge('subtitle');
    }
    // ── 无字幕 → 音频下载路径（慢）────────────────────────────
    else if (m.includes('未找到字幕') || m.includes('no subtitle') || m.includes('下载视频音频') || m.includes('下载音频')) {
      this.sp.stage = 'downloading';
      this.sp.target = 55;
      this._setModeBadge('whisper');
    }
    else if (m.includes('读取文本') || (m.includes('read') && m.includes('text'))) {
      this.sp.stage = 'parsing';
      this.sp.target = 55;
      this._setModeBadge('whisper');
    }
    else if (m.includes('转换音频') || m.includes('准备转录')) {
      this.sp.stage = 'downloading';
      this.sp.target = 55;
      this._setModeBadge('whisper');
    }
    else if (m.includes('上传') || m.includes('upload')) {
      this.sp.stage = 'preparing';
      this.sp.target = 40;
    }
    // ── 通用字幕检测中 ─────────────────────────────────────────
    else if (m.includes('检测') && (m.includes('字幕') || m.includes('subtitle'))) {
      this.sp.stage = 'subtitle';
      this.sp.target = 40;
    }
    // ── 其他阶段 ───────────────────────────────────────────────
    else if (m.includes('解析') || m.includes('pars'))                     { this.sp.stage = 'parsing';       this.sp.target = 60; }
    else if (m.includes('下载') || m.includes('download'))                 { this.sp.stage = 'downloading';   this.sp.target = 60; }
    else if (m.includes('转录') || m.includes('transcrib') || m.includes('whisper')) { this.sp.stage = 'transcribing';  this.sp.target = 80; }
    else if (m.includes('优化') || m.includes('optimiz'))                  { this.sp.stage = 'optimizing';    this.sp.target = 90; }
    else if (m.includes('摘要') || m.includes('summary'))                  { this.sp.stage = 'summarizing';   this.sp.target = 95; }
    else if (m.includes('完成') || m.includes('complet'))                  { this.sp.stage = 'completed';     this.sp.target = 100; }

    if (pct >= this.sp.target) this.sp.target = Math.min(pct + 8, 99);

    // ── DAG 路径与条件阶段识别 ──────────────────────────────
    if (m.includes('获取成功') || m.includes('subtitle found') || m.includes('字幕获取')) {
      this._setDagPath('subtitle');
    } else if (m.includes('未找到字幕') || m.includes('no subtitle') || m.includes('下载') || m.includes('download')) {
      this._setDagPath('whisper');
    }
    if (m.includes('翻译') || m.includes('translat')) {
      this._setDagTranslate(true);
    }
  }

  _setModeBadge(mode) {
    if (!this.modeBadge) return;
    if (mode === 'subtitle') {
      this.modeBadge.textContent  = this.t('mode_subtitle');
      this.modeBadge.className    = 'mode-badge subtitle';
      this.modeBadge.style.display = 'inline-block';
      if (this.progressFill) this.progressFill.classList.add('subtitle-mode');
    } else if (mode === 'whisper') {
      this.modeBadge.textContent  = this.t('mode_whisper');
      this.modeBadge.className    = 'mode-badge whisper';
      this.modeBadge.style.display = 'inline-block';
      if (this.progressFill) this.progressFill.classList.remove('subtitle-mode');
    }
  }

  _initSP() {
    this.sp.enabled = false; this.sp.current = 0; this.sp.target = 15;
    this.sp.lastServer = 0;  this.sp.startTime = Date.now(); this.sp.stage = 'preparing';
  }
  _startSP() {
    if (this.sp.interval) clearInterval(this.sp.interval);
    this.sp.enabled   = true;
    this.sp.startTime = this.sp.startTime || Date.now();
    this.sp.interval  = setInterval(() => this._tickSP(), 500);
  }
  _stopSP() {
    if (this.sp.interval) { clearInterval(this.sp.interval); this.sp.interval = null; }
    this.sp.enabled = false;
  }
  _tickSP() {
    if (!this.sp.enabled || this.sp.current >= this.sp.target) return;
    const speeds = { subtitle: .5, parsing: .3, downloading: .18, transcribing: .14, optimizing: .22, summarizing: .28 };
    let inc = speeds[this.sp.stage] || .2;
    const remaining = this.sp.target - this.sp.current;
    if (remaining < 5) inc *= .3;
    const next = Math.min(this.sp.current + inc, this.sp.target);
    if (next > this.sp.current) {
      this.sp.current = next;
      this._renderProgress(next, this._stageMsg());
    }
  }
  _stageMsg() {
    const map = {
      subtitle:       this.t('detecting_subtitles'),
      subtitle_found: this.t('subtitle_found'),
      downloading:    this.t('downloading_video'),
      parsing:        this.t('parsing_video'),
      transcribing:   this.t('transcribing_audio'),
      optimizing:     this.t('optimizing_transcript'),
      summarizing:    this.t('generating_summary'),
      completed:      this.t('completed'),
    };
    return map[this.sp.stage] || this.t('processing');
  }

  _renderProgress(pct, msg) {
    const p = Math.round(pct * 10) / 10;
    this.progressStatus.textContent = `${p}%`;
    this.progressFill.style.width   = `${p}%`;
    this._renderDag(pct);
  }

  /* ── Pipeline DAG (stage stepper) ─────────────────────── */
  _dagIcons() {
    return {
      check: '<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 5"/></svg>',
      spinner: '<svg class="ico spinning" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M8 2a6 6 0 1 1-5.2 3"/></svg>',
    };
  }

  _initDag() {
    this.dag = { path: 'whisper', translate: false, built: '', stages: [] };
    this._rebuildDag();
    this._renderDag(0);
  }

  _dagStageDefs() {
    const defs = [{ id: 'detect', start: 0, end: 15, kind: 'indet' }];
    if (this.dag.path === 'subtitle') {
      defs.push({ id: 'subtitle', start: 15, end: 55, kind: 'indet' });
    } else {
      defs.push({ id: 'download', start: 15, end: 40, kind: 'bar' });
      defs.push({ id: 'transcribe', start: 40, end: 55, kind: 'bar' });
    }
    const t = this.dag.translate;
    defs.push({ id: 'optimize', start: 55, end: t ? 70 : 80, kind: 'indet' });
    if (t) defs.push({ id: 'translate', start: 70, end: 80, kind: 'indet' });
    defs.push({ id: 'summary', start: 80, end: 99, kind: 'indet' });
    return defs;
  }

  _rebuildDag() {
    if (!this.dagList) return;
    const key = `${this.dag.path}|${this.dag.translate}`;
    if (this.dag.built === key) return;
    this.dag.built = key;
    this.dag.stages = this._dagStageDefs();
    this.dagList.innerHTML = '';
    this.dag.stages.forEach(s => {
      const node = document.createElement('div');
      node.className = 'dag-node';
      node.innerHTML = `
        <div class="dag-rail">
          <div class="dag-dot"></div>
          <div class="dag-line"></div>
        </div>
        <div class="dag-body">
          <div class="dag-head">
            <span class="dag-label">${this._esc(this.t('stage_' + s.id))}</span>
            <span class="dag-status"></span>
          </div>
          <div class="dag-bar${s.kind === 'indet' ? ' indet' : ''}" style="display:none;">
            <div class="dag-bar-fill"></div>
          </div>
        </div>`;
      this.dagList.appendChild(node);
      s.el = {
        node,
        dot: node.querySelector('.dag-dot'),
        status: node.querySelector('.dag-status'),
        bar: node.querySelector('.dag-bar'),
        fill: node.querySelector('.dag-bar-fill'),
      };
      s.state = 'pending';
    });
  }

  _dagDoing(id) {
    const map = {
      detect: 'detecting_subtitles',
      subtitle: 'fetching_subtitles',
      download: 'downloading_video',
      transcribe: 'transcribing_audio',
      optimize: 'optimizing_transcript',
      translate: 'generating_translation',
      summary: 'generating_summary',
    };
    return this.t(map[id]) || '';
  }

  _setDagPath(path) {
    if (!this.dag || this.dag.path === path) return;
    this.dag.path = path;
    this._rebuildDag();
    this._renderDag(this.sp.current);
  }

  _setDagTranslate(on) {
    if (!this.dag || this.dag.translate === on) return;
    this.dag.translate = on;
    this._rebuildDag();
    this._renderDag(this.sp.current);
  }

  _renderDag(pct) {
    if (!this.dag || !this.dag.stages.length) return;
    const icons = this._dagIcons();
    const complete = pct >= 99.5;
    let activeIdx = this.dag.stages.findIndex(s => pct < s.end);
    if (activeIdx === -1) activeIdx = this.dag.stages.length - 1;

    this.dag.stages.forEach((s, i) => {
      const done = complete || pct >= s.end;
      const active = !done && i === activeIdx;
      const state = done ? 'done' : active ? 'active' : 'pending';

      if (s.state !== state) {
        s.state = state;
        s.el.node.classList.remove('done', 'active');
        if (state !== 'pending') s.el.node.classList.add(state);
        s.el.dot.innerHTML = state === 'done' ? icons.check : state === 'active' ? icons.spinner : '';
      }

      if (done) {
        s.el.status.textContent = this.t('dag_done');
      } else if (active && s.kind === 'bar') {
        const p = Math.max(0, Math.min(100, ((pct - s.start) / (s.end - s.start)) * 100));
        s.el.status.textContent = `${this._dagDoing(s.id)} ${Math.round(p)}%`;
      } else if (active) {
        s.el.status.textContent = this._dagDoing(s.id);
      } else {
        s.el.status.textContent = '';
      }

      s.el.bar.style.display = state === 'pending' ? 'none' : '';
      if (s.kind === 'bar') {
        const p = done ? 100 : Math.max(0, Math.min(100, ((pct - s.start) / (s.end - s.start)) * 100));
        s.el.fill.style.width = `${p}%`;
      }
    });
  }

  _showProgress() {
    this.emptyState.style.display    = 'none';
    this.resultsPanel.classList.remove('show');
    this.progressPanel.classList.add('show');
    // Reset mode badge & progress bar color for new task
    if (this.modeBadge) { this.modeBadge.style.display = 'none'; this.modeBadge.className = 'mode-badge'; }
    if (this.progressFill) this.progressFill.classList.remove('subtitle-mode');
    this._initDag();
  }
  _hideProgress() { this.progressPanel.classList.remove('show'); }

  /* ── Results ──────────────────────────────────────────── */
  /** 与后端 Translator.normalize_lang_code 对齐，用于 Tab 展示判断 */
  _normLangTab(code) {
    if (!code) return '';
    const c = String(code).toLowerCase().trim();
    if (c.startsWith('zh')) return 'zh';
    if (c.length >= 2) return c.slice(0, 2);
    return c;
  }

  _showResults(script, summary, videoTitle, translation, detectedLang, summaryLang) {
    this.scriptContent.innerHTML  = script    ? marked.parse(script)      : '';
    this.summaryContent.innerHTML = summary   ? marked.parse(summary)     : '';

    const d = this._normLangTab(detectedLang);
    const s = this._normLangTab(summaryLang);
    const showTranslation = Boolean(translation) && d && s && d !== s;
    if (showTranslation) {
      this.translationContent.innerHTML = marked.parse(translation);
      this.translationTabBtn.style.display  = 'inline-block';
      this.dlTranslation.style.display      = 'inline-flex';
    } else {
      this.translationTabBtn.style.display  = 'none';
      this.dlTranslation.style.display      = 'none';
    }

    this.resultsPanel.classList.add('show');
    this._switchTab('script');
    this.resultsPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  _hideResults() { this.resultsPanel.classList.remove('show'); }

  /* ── Tabs ─────────────────────────────────────────────── */
  _switchTab(name) {
    this.tabBtns.forEach(b  => b.classList.toggle('active',  b.dataset.tab === name));
    this.tabPanes.forEach(p => p.classList.toggle('active', p.id === `${name}Tab`));
  }

  /* ── Download ─────────────────────────────────────────── */
  async _downloadFile(type) {
    if (!this.currentTaskId) { this._showError(this.t('error_no_download')); return; }
    try {
      const r = await fetch(`${this.apiBase}/task-status/${this.currentTaskId}`);
      if (!r.ok) throw new Error('Failed to get task status');
      const task = await r.json();

      let filename;
      if      (type === 'script')      filename = task.script_filename      || (task.script_path      ? task.script_path.split('/').pop()      : `transcript_${task.safe_title||'x'}_${task.short_id||'x'}.md`);
      else if (type === 'summary')     filename = task.summary_filename     || (task.summary_path     ? task.summary_path.split('/').pop()     : `summary_${task.safe_title||'x'}_${task.short_id||'x'}.md`);
      else if (type === 'translation') filename = task.translation_filename || (task.translation_path ? task.translation_path.split('/').pop() : `translation_${task.safe_title||'x'}_${task.short_id||'x'}.md`);
      else throw new Error('Unknown type');

      const a = document.createElement('a');
      a.href = task.output_folder
        ? `${this.apiBase}/download/${encodeURIComponent(task.output_folder)}/${encodeURIComponent(filename)}`
        : `${this.apiBase}/download/${encodeURIComponent(filename)}`;
      a.download = task.output_folder ? `${task.output_folder}_${filename}` : filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (e) {
      this._showError(this.t('error_download_failed') + e.message);
    }
  }

  /* ── Library (generated files) ────────────────────────── */
  _initLibrary() {
    if (!this.libraryCard) return;
    // 默认展开面板；记住用户折叠偏好
    const collapsed = localStorage.getItem('vt_lib_collapsed') === '1';
    this.libraryCard.classList.toggle('open', !collapsed);
    this.libraryHeader.setAttribute('aria-expanded', String(!collapsed));
    this._loadLibrary();
  }

  _toggleLibrary() {
    const open = this.libraryCard.classList.toggle('open');
    this.libraryHeader.setAttribute('aria-expanded', String(open));
    try { localStorage.setItem('vt_lib_collapsed', open ? '0' : '1'); } catch (_) {}
    if (open) this._loadLibrary();
  }

  async _loadLibrary() {
    try {
      const r = await fetch(`${this.apiBase}/files`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      this._renderLibrary(data.groups || []);
    } catch (e) {
      this.libraryGroupsEl.innerHTML = '';
      this.libraryEmpty.style.display = 'block';
      this.libraryEmpty.textContent = this.t('error_processing_failed') + (e.message || '');
    }
  }

  _renderLibrary(groups) {
    const total = groups.reduce((n, g) => n + g.files.length, 0);
    this.libraryCount.hidden = total === 0;
    this.libraryCount.textContent = String(total);

    if (!groups.length) {
      this.libraryGroupsEl.innerHTML = '';
      this.libraryEmpty.style.display = 'block';
      this.libraryEmpty.textContent = this.t('library_empty');
      return;
    }
    this.libraryEmpty.style.display = 'none';

    // 新分组默认展开第一个；保留用户已展开的分组
    if (!this._libOpenGroups.size && groups[0]) this._libOpenGroups.add(groups[0].folder);

    this.libraryGroupsEl.innerHTML = '';
    groups.forEach(g => {
      const groupEl = document.createElement('div');
      groupEl.className = 'lib-group' + (this._libOpenGroups.has(g.folder) ? ' open' : '');

      const head = document.createElement('div');
      head.className = 'lib-group-head';
      head.innerHTML = `
        <svg class="lib-group-chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 4l4 4-4 4"/></svg>
        <span class="lib-group-title">${this._esc(g.title || this.t('library_ungrouped'))}</span>
        <span class="lib-group-meta">${this.t('library_files_count')(g.files.length)} · ${this._fmtTime(g.mtime)}</span>`;
      head.addEventListener('click', () => {
        const open = groupEl.classList.toggle('open');
        if (open) this._libOpenGroups.add(g.folder);
        else this._libOpenGroups.delete(g.folder);
      });
      groupEl.appendChild(head);

      const filesEl = document.createElement('div');
      filesEl.className = 'lib-files';
      g.files.forEach(f => filesEl.appendChild(this._renderLibFile(g.folder, f)));
      groupEl.appendChild(filesEl);

      this.libraryGroupsEl.appendChild(groupEl);
    });
  }

  _renderLibFile(folder, f) {
    const row = document.createElement('div');
    row.className = 'lib-file';

    const kind = f.kind || 'other';
    const meta = `${this._fmtSize(f.size)} · ${this._fmtTime(f.mtime)}`;
    row.innerHTML = `
      <span class="lib-badge ${this._esc(kind)}">${this._esc(this.t(`kind_${kind}`) || kind)}</span>
      <span class="lib-file-name">${this._esc(f.name)}</span>
      ${f.model ? `<span class="lib-file-model">${this._esc(f.model)}</span>` : ''}
      <span class="lib-file-meta">${this._esc(meta)}</span>
      <span class="lib-file-actions">
        <button type="button" class="lib-icon-btn lib-act-dl" title="${this._esc(this.t('library_download'))}">
          <svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M8 2.5v8M5 7.5l3 3 3-3"/><path d="M2.5 12.5h11"/></svg>
        </button>
        <button type="button" class="lib-icon-btn danger lib-act-del" title="${this._esc(this.t('library_delete'))}">
          <svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2.5 4h11"/><path d="M6.5 4V2.5h3V4"/><path d="M4 4l.7 9.5h6.6L12 4"/></svg>
        </button>
      </span>`;

    row.addEventListener('click', () => this._openLibPreview(folder, f.name));
    row.querySelector('.lib-act-dl').addEventListener('click', (e) => {
      e.stopPropagation();
      this._downloadLibFile(folder, f.name);
    });
    row.querySelector('.lib-act-del').addEventListener('click', (e) => {
      e.stopPropagation();
      this._deleteLibFile(folder, f.name);
    });
    return row;
  }

  async _openLibPreview(folder, name) {
    try {
      const r = await fetch(`${this.apiBase}/files/${encodeURIComponent(folder)}/${encodeURIComponent(name)}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const f = await r.json();
      this._libModalFile = { folder, name };
      const kind = f.kind || 'other';
      this.libModalBadge.className = `lib-badge ${kind}`;
      this.libModalBadge.textContent = this.t(`kind_${kind}`) || kind;
      this.libModalTitle.textContent = name;
      this.libModalBody.innerHTML = marked.parse(f.content || '');
      this.libModalBackdrop.classList.add('show');
      document.body.style.overflow = 'hidden';
    } catch (e) {
      this._showError(this.t('error_processing_failed') + (e.message || ''));
    }
  }

  _closeLibModal() {
    this.libModalBackdrop.classList.remove('show');
    document.body.style.overflow = '';
    this._libModalFile = null;
  }

  _downloadLibFile(folder, name) {
    const a = document.createElement('a');
    const isRoot = folder === '_root';
    a.href = isRoot
      ? `${this.apiBase}/download/${encodeURIComponent(name)}`
      : `${this.apiBase}/download/${encodeURIComponent(folder)}/${encodeURIComponent(name)}`;
    a.download = isRoot ? name : `${folder}_${name}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  async _deleteLibFile(folder, name) {
    if (!window.confirm(this.t('library_delete_file_confirm'))) return;
    try {
      const r = await fetch(`${this.apiBase}/files/${encodeURIComponent(folder)}/${encodeURIComponent(name)}`, { method: 'DELETE' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      if (this._libModalFile?.folder === folder && this._libModalFile?.name === name) this._closeLibModal();
      await this._loadLibrary();
    } catch (e) {
      this._showError(this.t('error_processing_failed') + (e.message || ''));
    }
  }

  /* ── Library helpers ──────────────────────────────────── */
  _esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }
  _fmtSize(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(1)} MB`;
  }
  _fmtTime(mtime) {
    if (!mtime) return '';
    const d = new Date(mtime * 1000);
    const pad = (x) => String(x).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  /* ── UI helpers ───────────────────────────────────────── */
  _setLoading(on) {
    this.submitBtn.disabled = on;
    this.submitBtn.innerHTML = on
      ? `<span class="spinner"></span> ${this.t('processing')}`
      : `<svg class="ico" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5L14 14"/></svg> <span>${this.t('start_transcription')}</span>`;
    if (this.uploadPickBtn) this.uploadPickBtn.disabled = on;
    if (this.uploadZone) {
      this.uploadZone.style.pointerEvents = on ? 'none' : '';
      this.uploadZone.style.opacity = on ? '0.65' : '';
      this.uploadZone.tabIndex = on ? -1 : 0;
    }
    if (this.fileInput) this.fileInput.disabled = on;
  }

  _showError(msg) {
    this.errorMsg.textContent = msg;
    this.errorBanner.classList.add('show');
    this.errorBanner.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    setTimeout(() => this._hideError(), 6000);
  }
  _hideError() { this.errorBanner.classList.remove('show'); }

  _debounce(fn, ms) {
    let t;
    return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
  }
}

/* ── Boot ──────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  window.vt = new VideoTranscriber();
});

window.addEventListener('beforeunload', () => {
  if (window.vt?.eventSource) window.vt._stopSSE();
});
