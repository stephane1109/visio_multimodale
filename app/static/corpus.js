(function () {
  const state = {
    corpora: [],
    activeCorpusId: "",
  };

  const elements = {
    tabDashboardButton: document.getElementById("tab-dashboard-button"),
    tabCorpusButton: document.getElementById("tab-corpus-button"),
    tabHelpButton: document.getElementById("tab-help-button"),
    launchWarningPanel: document.getElementById("launch-warning-panel"),
    launchWarningText: document.getElementById("launch-warning-text"),
    corpusImportPanel: document.getElementById("corpus-import-panel"),
    corpusImportForm: document.getElementById("corpus-import-form"),
    corpusTitleInput: document.getElementById("corpus-title-input"),
    corpusAudioFileInput: document.getElementById("corpus-audio-file-input"),
    corpusTranscriptFileInput: document.getElementById("corpus-transcript-file-input"),
    openCorporaButton: document.getElementById("open-corpora-button"),
    corpusImportStatus: document.getElementById("corpus-import-status"),
    corporaList: document.getElementById("corpora-list"),
    corpusEditorEmpty: document.getElementById("corpus-editor-empty"),
    corpusEditor: document.getElementById("corpus-editor"),
    corpusEditorTitle: document.getElementById("corpus-editor-title"),
    corpusEditorMeta: document.getElementById("corpus-editor-meta"),
    corpusAudioPlayer: document.getElementById("corpus-audio-player"),
    corpusTranscriptEditor: document.getElementById("corpus-transcript-editor"),
    saveCorpusTranscriptButton: document.getElementById("save-corpus-transcript-button"),
    openActiveCorpusButton: document.getElementById("open-active-corpus-button"),
    corpusEditorStatus: document.getElementById("corpus-editor-status"),
  };

  function setStatus(target, message, tone) {
    target.textContent = message;
    target.dataset.tone = tone || "neutral";
  }

  function isFileMode() {
    return window.location.protocol === "file:";
  }

  function renderLaunchWarning() {
    if (isFileMode()) {
      elements.launchWarningPanel.hidden = false;
      setStatus(
        elements.launchWarningText,
        "Vous avez ouvert corpus.html en file://. Utilisez Lancer.command puis ouvrez http://127.0.0.1:8000/corpus.html.",
        "error",
      );
      return;
    }
    elements.launchWarningPanel.hidden = true;
  }

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function formatDate(value) {
    if (!value) {
      return "—";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return date.toLocaleString("fr-FR");
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || payload.message || "Erreur inattendue.");
    }
    return payload;
  }

  function renderCorpora(corpora) {
    if (!corpora.length) {
      elements.corporaList.innerHTML = '<p class="empty-line">Aucun corpus importé pour le moment.</p>';
      return;
    }

    elements.corporaList.innerHTML = corpora
      .map((corpus) => {
        const active = corpus.corpus_id === state.activeCorpusId;
        return `
          <article class="item-card ${active ? "item-card-active" : ""}">
            <div class="item-head">
              <div>
                <strong>${escapeHtml(corpus.title || corpus.corpus_id)}</strong>
                <p class="mini-line">Créé le ${formatDate(corpus.created_at)}</p>
              </div>
              <span class="badge" data-tone="${corpus.transcript_available ? "success" : "neutral"}">
                ${corpus.transcript_available ? "texte" : "audio seul"}
              </span>
            </div>
            <p class="mini-line">Audio : ${escapeHtml(corpus.audio_filename || "—")}</p>
            <p class="mini-line">Fichiers : ${(corpus.files || []).map(escapeHtml).join(", ") || "—"}</p>
            <div class="actions">
              <button class="secondary-button small-button" type="button" data-action="open-corpus" data-corpus-id="${escapeHtml(corpus.corpus_id)}">
                Ouvrir
              </button>
              <button class="secondary-button small-button" type="button" data-action="open-corpus-folder" data-corpus-id="${escapeHtml(corpus.corpus_id)}">
                Ouvrir le dossier
              </button>
            </div>
          </article>
        `;
      })
      .join("");
  }

  function clearCorpusEditor() {
    state.activeCorpusId = "";
    elements.corpusEditor.hidden = true;
    elements.corpusEditorEmpty.hidden = false;
    elements.corpusEditorTitle.textContent = "—";
    elements.corpusEditorMeta.textContent = "—";
    elements.corpusAudioPlayer.removeAttribute("src");
    elements.corpusAudioPlayer.load();
    elements.corpusTranscriptEditor.value = "";
  }

  function renderCorpusDetail(corpus) {
    state.activeCorpusId = corpus.corpus_id;
    elements.corpusEditor.hidden = false;
    elements.corpusEditorEmpty.hidden = true;
    elements.corpusEditorTitle.textContent = corpus.title || corpus.corpus_id;
    elements.corpusEditorMeta.textContent = `Créé le ${formatDate(corpus.created_at)} | Mis à jour le ${formatDate(corpus.updated_at)}`;
    elements.corpusAudioPlayer.src = corpus.audio_url ? `${corpus.audio_url}&v=${Date.now()}` : "";
    elements.corpusTranscriptEditor.value = corpus.transcript_text || "";
    setStatus(elements.corpusEditorStatus, "Le corpus est chargé. Vous pouvez écouter l'audio et modifier le texte.", "success");
    renderCorpora(state.corpora);
  }

  async function refreshCorpora() {
    try {
      const payload = await fetchJson("/api/corpora");
      state.corpora = payload.corpora || [];
      renderCorpora(state.corpora);
      if (!state.activeCorpusId) {
        clearCorpusEditor();
        return;
      }
      const exists = state.corpora.some((corpus) => corpus.corpus_id === state.activeCorpusId);
      if (!exists) {
        clearCorpusEditor();
      } else {
        renderCorpora(state.corpora);
      }
    } catch (error) {
      state.corpora = [];
      renderCorpora([]);
      clearCorpusEditor();
      setStatus(elements.corpusImportStatus, error.message || "Impossible de charger les corpus.", "error");
    }
  }

  async function openCorpus(corpusId) {
    try {
      const payload = await fetchJson(`/api/corpora/detail?corpus_id=${encodeURIComponent(corpusId)}`);
      renderCorpusDetail(payload.corpus);
    } catch (error) {
      setStatus(elements.corpusEditorStatus, error.message || "Impossible d'ouvrir le corpus.", "error");
    }
  }

  async function importCorpus(event) {
    event.preventDefault();
    const title = elements.corpusTitleInput.value.trim();
    const audioFile = elements.corpusAudioFileInput.files[0];

    if (!title) {
      setStatus(elements.corpusImportStatus, "Le nom du corpus est requis.", "error");
      return;
    }
    if (!audioFile) {
      setStatus(elements.corpusImportStatus, "La piste audio est requise.", "error");
      return;
    }

    const formData = new FormData();
    formData.append("corpus_title", title);
    formData.append("audio_file", audioFile, audioFile.name);
    const transcriptFile = elements.corpusTranscriptFileInput.files[0];
    if (transcriptFile) {
      formData.append("transcript_file", transcriptFile, transcriptFile.name);
    }

    try {
      const payload = await fetchJson("/api/corpora/import", {
        method: "POST",
        body: formData,
      });
      elements.corpusImportForm.reset();
      setStatus(elements.corpusImportStatus, payload.message, "success");
      await refreshCorpora();
      await openCorpus(payload.corpus.corpus_id);
    } catch (error) {
      setStatus(elements.corpusImportStatus, error.message || "Impossible d'importer le corpus.", "error");
    }
  }

  async function saveCorpusTranscript() {
    if (!state.activeCorpusId) {
      setStatus(elements.corpusEditorStatus, "Ouvrez d'abord un corpus.", "error");
      return;
    }

    try {
      const payload = await fetchJson("/api/corpora/save-transcript", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          corpus_id: state.activeCorpusId,
          transcript_text: elements.corpusTranscriptEditor.value,
        }),
      });
      setStatus(elements.corpusEditorStatus, payload.message, "success");
      await refreshCorpora();
      await openCorpus(state.activeCorpusId);
    } catch (error) {
      setStatus(elements.corpusEditorStatus, error.message || "Impossible d'enregistrer le texte.", "error");
    }
  }

  async function openFolder(target) {
    try {
      const payload = await fetchJson("/api/admin/open-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target }),
      });
      setStatus(elements.corpusImportStatus, payload.message, "success");
    } catch (error) {
      setStatus(elements.corpusImportStatus, error.message || "Impossible d'ouvrir le dossier.", "error");
    }
  }

  function bindListActions() {
    elements.corporaList.addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) {
        return;
      }
      const { action, corpusId } = button.dataset;
      if (action === "open-corpus" && corpusId) {
        await openCorpus(corpusId);
      }
      if (action === "open-corpus-folder" && corpusId) {
        await openFolder(`corpus:${corpusId}`);
      }
    });
  }

  function bindEvents() {
    elements.tabDashboardButton.addEventListener("click", () => {
      window.location.href = "/admin.html";
    });
    elements.tabCorpusButton.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: "auto" });
      elements.corpusTitleInput.focus();
    });
    elements.tabHelpButton.addEventListener("click", () => {
      window.location.href = "/aide.html";
    });
    elements.corpusImportForm.addEventListener("submit", importCorpus);
    elements.openCorporaButton.addEventListener("click", () => openFolder("corpora"));
    elements.saveCorpusTranscriptButton.addEventListener("click", saveCorpusTranscript);
    elements.openActiveCorpusButton.addEventListener("click", () => {
      if (!state.activeCorpusId) {
        setStatus(elements.corpusEditorStatus, "Ouvrez d'abord un corpus.", "error");
        return;
      }
      openFolder(`corpus:${state.activeCorpusId}`);
    });
    bindListActions();
  }

  bindEvents();
  renderLaunchWarning();
  if (!isFileMode()) {
    refreshCorpora();
  }
})();
