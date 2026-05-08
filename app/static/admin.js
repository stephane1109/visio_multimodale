(function () {
  const state = {
    overview: null,
    livekitInvestigatorLink: "",
  };

  const elements = {
    tabDashboardButton: document.getElementById("tab-dashboard-button"),
    tabCorpusButton: document.getElementById("tab-corpus-button"),
    tabHelpButton: document.getElementById("tab-help-button"),
    launchWarningPanel: document.getElementById("launch-warning-panel"),
    launchWarningText: document.getElementById("launch-warning-text"),
    serverStatus: document.getElementById("server-status"),
    ffmpegStatus: document.getElementById("ffmpeg-status"),
    ngrokStatus: document.getElementById("ngrok-status"),
    startNgrokButton: document.getElementById("start-ngrok-button"),
    stopNgrokButton: document.getElementById("stop-ngrok-button"),
    ngrokAuthtokenInput: document.getElementById("ngrok-authtoken-input"),
    saveNgrokAuthtokenButton: document.getElementById("save-ngrok-authtoken-button"),
    ngrokStatusLine: document.getElementById("ngrok-status-line"),
    livekitSettingsForm: document.getElementById("livekit-settings-form"),
    livekitUrlInput: document.getElementById("livekit-url-input"),
    livekitApiKeyInput: document.getElementById("livekit-api-key-input"),
    livekitApiSecretInput: document.getElementById("livekit-api-secret-input"),
    livekitSettingsStatus: document.getElementById("livekit-settings-status"),
    whisperSmallRadio: document.getElementById("whisper-small-radio"),
    whisperMediumRadio: document.getElementById("whisper-medium-radio"),
    saveWhisperButton: document.getElementById("save-whisper-button"),
    whisperStatus: document.getElementById("whisper-status"),
    livekitSessionForm: document.getElementById("livekit-session-form"),
    livekitParticipantCodeInput: document.getElementById("livekit-participant-code-input"),
    livekitParticipantRoleLabelInput: document.getElementById("livekit-participant-role-label-input"),
    livekitInvestigatorRoleLabelInput: document.getElementById("livekit-investigator-role-label-input"),
    livekitNotesInput: document.getElementById("livekit-notes-input"),
    livekitLinksResult: document.getElementById("livekit-links-result"),
    livekitParticipantLinkOutput: document.getElementById("livekit-participant-link-output"),
    openLivekitInvestigatorLinkButton: document.getElementById("open-livekit-investigator-link-button"),
    copyLivekitParticipantLinkButton: document.getElementById("copy-livekit-participant-link-button"),
    livekitSessionStatus: document.getElementById("livekit-session-status"),
    overviewStatus: document.getElementById("overview-status"),
    openSessionsButton: document.getElementById("open-sessions-button"),
    refreshButton: document.getElementById("refresh-button"),
    sessionsList: document.getElementById("sessions-list"),
  };

  function setStatus(target, message, tone) {
    target.textContent = message;
    target.dataset.tone = tone || "neutral";
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

  function formatBytes(value) {
    if (!value || value <= 0) {
      return "—";
    }
    const units = ["B", "KB", "MB", "GB"];
    let size = value;
    let unitIndex = 0;
    while (size >= 1024 && unitIndex < units.length - 1) {
      size /= 1024;
      unitIndex += 1;
    }
    return `${size.toFixed(unitIndex === 0 ? 0 : 1)} ${units[unitIndex]}`;
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const payload = await response.json();
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || payload.message || "Erreur inattendue.");
    }
    return payload;
  }

  function isFileMode() {
    return window.location.protocol === "file:";
  }

  function renderLaunchWarning() {
    if (isFileMode()) {
      elements.launchWarningPanel.hidden = false;
      setStatus(
        elements.launchWarningText,
        "Vous avez ouvert admin.html en file://. Utilisez Lancer.command puis ouvrez http://127.0.0.1:8000/admin.html.",
        "error",
      );
      return;
    }
    elements.launchWarningPanel.hidden = true;
  }

  function renderWhisperSelection() {
    const current = (state.overview && state.overview.whisper_model_size) || "small";
    elements.whisperSmallRadio.checked = current === "small";
    elements.whisperMediumRadio.checked = current === "medium";
    setStatus(
      elements.whisperStatus,
      `Modèle actuel : ${current}. Ce réglage sera utilisé pour les prochaines transcriptions.`,
      "success",
    );
  }

  function renderNgrokStatus() {
    const overview = state.overview || {};
    const running = Boolean(overview.ngrok_running);

    elements.ngrokStatus.textContent = running ? "Actif" : overview.ngrok_available ? "Disponible" : "Absent";
    elements.startNgrokButton.disabled = !overview.ngrok_available || running;
    elements.stopNgrokButton.disabled = !running;

    if (running && overview.ngrok_public_url) {
      setStatus(elements.ngrokStatusLine, "Accès distant actif. Vous pouvez créer la session.", "success");
      return;
    }

    if (!overview.ngrok_available) {
      setStatus(elements.ngrokStatusLine, "ngrok n'est pas installé sur cette machine.", "error");
      return;
    }

    setStatus(elements.ngrokStatusLine, "Accès distant inactif.", "neutral");
  }

  function renderLivekitSettings() {
    const overview = state.overview || {};
    elements.livekitUrlInput.value = overview.livekit_url || elements.livekitUrlInput.value || "";
    setStatus(
      elements.livekitSettingsStatus,
      overview.livekit_configured
        ? "LiveKit est configuré. Vous pouvez créer une session split-screen."
        : "LiveKit n'est pas encore configuré. Renseignez l'URL, l'API Key et l'API Secret.",
      overview.livekit_configured ? "success" : "error",
    );
  }

  function renderSessions(sessions) {
    if (!sessions.length) {
      elements.sessionsList.innerHTML = '<p class="empty-line">Aucune session reçue pour le moment.</p>';
      return;
    }

    elements.sessionsList.innerHTML = sessions
      .map((session) => {
        const processing = session.processing || {};
        const status = processing.status || "unknown";
        const step = processing.step || "";
        return `
          <article class="item-card">
            <div class="item-head">
              <div>
                <strong>${session.session_id}</strong>
                <p class="mini-line">Participant : ${session.participant_code || "—"}</p>
              </div>
              <span class="badge" data-tone="${status === "completed" ? "success" : status === "failed" ? "error" : "neutral"}">${status}</span>
            </div>
            <p class="mini-line">Créée le ${formatDate(session.created_at)}</p>
            <p class="mini-line">Taille envoyée : ${formatBytes(session.upload_size_bytes)}</p>
            <p class="mini-line">Étape : ${step || "—"}</p>
            <p class="mini-line">Fichiers : ${(session.files || []).join(", ") || "—"}</p>
            <div class="actions">
              <button class="secondary-button small-button" type="button" data-action="open-session" data-session-id="${session.session_id}">
                Ouvrir ce dossier
              </button>
            </div>
          </article>
        `;
      })
      .join("");
  }

  async function refreshDashboard() {
    try {
      const [overviewPayload, sessionsPayload] = await Promise.all([
        fetchJson("/api/admin/overview"),
        fetchJson("/api/admin/sessions"),
      ]);

      state.overview = overviewPayload;
      document.title = `${overviewPayload.title} - Entretien à distance`;
      elements.serverStatus.textContent = "Actif";
      elements.ffmpegStatus.textContent = overviewPayload.ffmpeg_available ? "Disponible" : "Absent";

      setStatus(
        elements.overviewStatus,
        `Application prête. Sessions enregistrées : ${overviewPayload.sessions_count}`,
        "success",
      );

      renderSessions(sessionsPayload.sessions || []);
      renderWhisperSelection();
      renderNgrokStatus();
      renderLivekitSettings();
    } catch (error) {
      elements.serverStatus.textContent = "Erreur";
      renderSessions([]);
      setStatus(elements.overviewStatus, error.message || "Impossible de charger le tableau de bord.", "error");
    }
  }

  async function saveLivekitSettings(event) {
    event.preventDefault();
    const payload = {
      livekit_url: elements.livekitUrlInput.value.trim(),
      livekit_api_key: elements.livekitApiKeyInput.value.trim(),
      livekit_api_secret: elements.livekitApiSecretInput.value.trim(),
    };

    try {
      const response = await fetchJson("/api/admin/livekit-settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!state.overview) {
        state.overview = {};
      }
      state.overview.livekit_url = response.settings.livekit_url;
      state.overview.livekit_configured = response.settings.livekit_configured;
      renderLivekitSettings();
      setStatus(elements.livekitSettingsStatus, response.message, "success");
    } catch (error) {
      setStatus(elements.livekitSettingsStatus, error.message || "Impossible d'enregistrer LiveKit.", "error");
    }
  }

  async function saveWhisperSettings() {
    const whisperModelSize = elements.whisperMediumRadio.checked ? "medium" : "small";
    try {
      const payload = await fetchJson("/api/admin/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ whisper_model_size: whisperModelSize }),
      });
      if (!state.overview) {
        state.overview = {};
      }
      state.overview.whisper_model_size = payload.settings.whisper_model_size;
      renderWhisperSelection();
      setStatus(elements.whisperStatus, payload.message, "success");
    } catch (error) {
      setStatus(elements.whisperStatus, error.message || "Impossible d'enregistrer le choix Whisper.", "error");
    }
  }

  async function createLivekitSession(event) {
    event.preventDefault();
    if (isFileMode()) {
      setStatus(
        elements.livekitSessionStatus,
        "Ouvrez d'abord l'application via http://127.0.0.1:8000/admin.html, pas via file://.",
        "error",
      );
      return;
    }

    const payload = {
      participant_code: elements.livekitParticipantCodeInput.value.trim(),
      participant_role_label: elements.livekitParticipantRoleLabelInput.value.trim(),
      investigator_role_label: elements.livekitInvestigatorRoleLabelInput.value.trim(),
      notes: elements.livekitNotesInput.value.trim(),
    };

    if (!state.overview?.ngrok_running || !state.overview?.ngrok_public_url) {
      try {
        const ngrokPayload = await fetchJson("/api/admin/ngrok/start", {
          method: "POST",
        });
        if (!state.overview) {
          state.overview = {};
        }
        state.overview.ngrok_available = true;
        state.overview.ngrok_running = true;
        state.overview.ngrok_public_url = ngrokPayload.public_url;
        renderNgrokStatus();
        setStatus(elements.livekitSessionStatus, "Accès distant démarré automatiquement avec ngrok.", "success");
      } catch (error) {
        setStatus(
          elements.livekitSessionStatus,
          error.message || "Impossible de démarrer automatiquement ngrok.",
          "error",
        );
        return;
      }
    }

    try {
      const response = await fetchJson("/api/admin/create-livekit-session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      elements.livekitLinksResult.hidden = false;
      state.livekitInvestigatorLink = response.investigator_link;
      elements.livekitParticipantLinkOutput.value = response.participant_link;
      setStatus(elements.livekitSessionStatus, "Session créée. Copiez maintenant le lien participant.", "success");
      await refreshDashboard();
    } catch (error) {
      setStatus(elements.livekitSessionStatus, error.message || "Impossible de créer la session LiveKit.", "error");
    }
  }

  async function startNgrok() {
    try {
      const payload = await fetchJson("/api/admin/ngrok/start", {
        method: "POST",
      });
      if (!state.overview) {
        state.overview = {};
      }
      state.overview.ngrok_available = true;
      state.overview.ngrok_running = true;
      state.overview.ngrok_public_url = payload.public_url;
      renderNgrokStatus();
      setStatus(elements.overviewStatus, payload.message, "success");
      setStatus(elements.ngrokStatusLine, payload.message, "success");
    } catch (error) {
      setStatus(elements.overviewStatus, error.message || "Impossible de démarrer ngrok.", "error");
      setStatus(elements.ngrokStatusLine, error.message || "Impossible de démarrer ngrok.", "error");
    }
  }

  async function stopNgrok() {
    try {
      const payload = await fetchJson("/api/admin/ngrok/stop", {
        method: "POST",
      });
      if (!state.overview) {
        state.overview = {};
      }
      state.overview.ngrok_running = false;
      state.overview.ngrok_public_url = "";
      renderNgrokStatus();
      setStatus(elements.overviewStatus, payload.message, "success");
      setStatus(elements.ngrokStatusLine, payload.message, "success");
    } catch (error) {
      setStatus(elements.overviewStatus, error.message || "Impossible d'arrêter ngrok.", "error");
      setStatus(elements.ngrokStatusLine, error.message || "Impossible d'arrêter ngrok.", "error");
    }
  }

  async function saveNgrokAuthtoken() {
    const authtoken = elements.ngrokAuthtokenInput.value.trim();
    if (!authtoken) {
      setStatus(elements.ngrokStatusLine, "Collez d'abord l'authtoken ngrok.", "error");
      return;
    }

    try {
      const payload = await fetchJson("/api/admin/ngrok/authtoken", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ authtoken }),
      });
      elements.ngrokAuthtokenInput.value = "";
      setStatus(elements.ngrokStatusLine, payload.message, "success");
      setStatus(elements.overviewStatus, payload.message, "success");
      await startNgrok();
    } catch (error) {
      setStatus(elements.overviewStatus, error.message || "Impossible d'enregistrer l'authtoken ngrok.", "error");
      setStatus(elements.ngrokStatusLine, error.message || "Impossible d'enregistrer l'authtoken ngrok.", "error");
    }
  }

  async function copyTextValue(text, successTarget, message) {
    if (!text) {
      return;
    }
    await navigator.clipboard.writeText(text);
    setStatus(successTarget, message, "success");
  }

  async function openFolder(target) {
    try {
      const payload = await fetchJson("/api/admin/open-folder", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target }),
      });
      setStatus(elements.overviewStatus, payload.message, "success");
    } catch (error) {
      setStatus(elements.overviewStatus, error.message || "Impossible d'ouvrir le dossier.", "error");
    }
  }

  function bindListActions() {
    elements.sessionsList.addEventListener("click", async (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) {
        return;
      }
      const { action, sessionId } = button.dataset;
      if (action === "open-session" && sessionId) {
        await openFolder(`session:${sessionId}`);
      }
    });
  }

  function bindEvents() {
    elements.tabDashboardButton.addEventListener("click", () => {
      window.location.href = "/admin.html";
    });
    elements.tabCorpusButton.addEventListener("click", () => {
      window.location.href = "/corpus.html";
    });
    elements.tabHelpButton.addEventListener("click", () => {
      window.location.href = "/aide.html";
    });
    elements.livekitSettingsForm.addEventListener("submit", saveLivekitSettings);
    elements.saveWhisperButton.addEventListener("click", saveWhisperSettings);
    elements.livekitSessionForm.addEventListener("submit", createLivekitSession);
    elements.openLivekitInvestigatorLinkButton.addEventListener("click", () => {
      if (!state.livekitInvestigatorLink) {
        setStatus(elements.livekitSessionStatus, "Créez d'abord une session LiveKit.", "error");
        return;
      }
      window.open(state.livekitInvestigatorLink, "_blank", "noopener");
      setStatus(elements.livekitSessionStatus, "Salle enquêteur ouverte.", "success");
    });
    elements.copyLivekitParticipantLinkButton.addEventListener("click", () =>
      copyTextValue(elements.livekitParticipantLinkOutput.value, elements.livekitSessionStatus, "Lien participant copié."),
    );
    elements.refreshButton.addEventListener("click", refreshDashboard);
    elements.openSessionsButton.addEventListener("click", () => openFolder("sessions"));
    elements.startNgrokButton.addEventListener("click", startNgrok);
    elements.stopNgrokButton.addEventListener("click", stopNgrok);
    elements.saveNgrokAuthtokenButton.addEventListener("click", saveNgrokAuthtoken);
    bindListActions();
  }

  bindEvents();
  renderLaunchWarning();
  if (!isFileMode()) {
    refreshDashboard();
  } else {
    elements.serverStatus.textContent = "Hors serveur";
    setStatus(
      elements.overviewStatus,
      "Mode file:// détecté. Lancez l'application puis utilisez http://127.0.0.1:8000/admin.html.",
      "error",
    );
  }
})();
