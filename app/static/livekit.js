(function () {
  const query = new URLSearchParams(window.location.search);
  const role = (query.get("role") || "participant").toLowerCase();
  const sessionId = query.get("session_id") || "";
  const isParticipant = role === "participant";

  document.body.dataset.livekitRole = isParticipant ? "participant" : "investigator";

  const state = {
    role,
    sessionId,
    connected: false,
    room: null,
    livekitClient: null,
    credentials: null,
    connectRequested: false,
    participantRecorder: null,
    participantChunks: [],
    investigatorRecorder: null,
    investigatorChunks: [],
    startedAt: null,
    timerHandle: null,
    uploadDone: false,
  };

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function buildRoleCopy() {
    if (isParticipant) {
      return {
        eyebrow: "Live Session",
        title: "Salle participant",
        intro:
          "Vous rejoignez ici l'entretien en ligne. Le chercheur vous voit en direct, vous voyez sa webcam, et vous donnez votre consentement avant le demarrage de l'enregistrement.",
        connectionHint: 'Cliquez sur "Rejoindre la session" pour activer votre camera et votre micro.',
        roomHint: "Validez le consentement puis rejoignez la salle.",
        localLabel: "Votre flux",
        localBadge: "vous",
        remoteLabel: "Flux enqueteur",
        remoteBadge: "chercheur",
        remoteEmpty: "Le chercheur n'est pas encore connecte.",
      };
    }

    return {
      eyebrow: "Control Room",
      title: "Salle enqueteur",
      intro:
        "Vous pilotez ici l'entretien en ligne. Les deux webcams sont visibles dans un ecran split, mais seul le flux video du participant est enregistre, avec l'audio des deux roles et la transcription texte.",
      connectionHint: 'Cliquez sur "Rejoindre la session" pour activer la salle enqueteur.',
      roomHint: "La salle enqueteur est prete des que la configuration LiveKit est valide.",
      localLabel: "Votre flux",
      localBadge: "chercheur",
      remoteLabel: "Flux participant",
      remoteBadge: "participant",
      remoteEmpty: "Le participant n'est pas encore connecte.",
    };
  }

  const roleCopy = buildRoleCopy();

  const root = document.getElementById("livekit-app-root");
  root.innerHTML = `
    <main class="page-shell">
      <header class="hero">
        <p class="eyebrow">${escapeHtml(roleCopy.eyebrow)}</p>
        <h1>${escapeHtml(roleCopy.title)}</h1>
        <p class="intro">${escapeHtml(roleCopy.intro)}</p>
      </header>

      <div class="livekit-room-grid">
        <section class="panel panel-focus livekit-panel livekit-panel-session">
          <h2>Session</h2>
          <div class="recording-meta">
            <div>
              <span class="meta-label">Role</span>
              <strong id="role-label">${isParticipant ? "Participant" : "Enqueteur"}</strong>
            </div>
            <div>
              <span class="meta-label">Session</span>
              <strong id="session-label">${escapeHtml(sessionId || "—")}</strong>
            </div>
          </div>
          <p id="room-status" class="status-line">Chargement des identifiants LiveKit…</p>
        </section>

        ${
          isParticipant
            ? `
              <section class="panel panel-focus livekit-panel livekit-panel-consent" id="participant-consent-panel">
                <h2>Consentement</h2>
                <label class="checkbox-row">
                  <input id="participant-consent-checkbox" type="checkbox" />
                  <span>J'accepte l'enregistrement de ma video et de mon audio pour cet entretien.</span>
                </label>
                <p class="mini-line">
                  La webcam des deux personnes peut etre affichee a l'ecran, mais seule la video du participant est sauvegardee.
                </p>
                <p id="consent-status" class="status-line">Le consentement n'est pas encore donne.</p>
              </section>
            `
            : ""
        }

        <section class="panel panel-focus livekit-panel livekit-panel-connection">
          <h2>Connexion et capture</h2>
          <div class="actions livekit-action-wrap" id="connection-actions">
            <button id="connect-button" class="primary-button" type="button">Rejoindre la session</button>
            <button id="disconnect-button" class="secondary-button livekit-track-button" type="button" disabled>Quitter la session</button>
            <button id="toggle-camera-button" class="secondary-button livekit-track-button" type="button" disabled>Camera</button>
            <button id="toggle-mic-button" class="secondary-button livekit-track-button" type="button" disabled>Micro</button>
            <button id="start-audio-button" class="secondary-button start-audio-button" type="button" disabled>Activer le son</button>
          </div>
          <p id="connection-status" class="status-line">${escapeHtml(roleCopy.connectionHint)}</p>
        </section>

        <section class="panel panel-focus livekit-panel livekit-panel-stage">
          <h2>Ecran split</h2>
          <div class="video-stage">
            <article class="video-card">
              <div class="item-head">
                <strong>${escapeHtml(roleCopy.localLabel)}</strong>
                <span class="badge" data-tone="neutral" id="local-badge">${escapeHtml(roleCopy.localBadge)}</span>
              </div>
              <div id="local-video-slot" class="video-slot empty-slot">Connexion non demarree</div>
            </article>
            <article class="video-card">
              <div class="item-head">
                <strong>${escapeHtml(roleCopy.remoteLabel)}</strong>
                <span class="badge" data-tone="neutral" id="remote-badge">${escapeHtml(roleCopy.remoteBadge)}</span>
              </div>
              <div id="remote-video-slot" class="video-slot empty-slot">${escapeHtml(roleCopy.remoteEmpty)}</div>
            </article>
          </div>
        </section>

        ${
          isParticipant
            ? ""
            : `
              <section class="panel panel-focus livekit-panel livekit-panel-recording">
                <h2>Enregistrement</h2>
                <p class="intro compact-intro">
                  L'enregistrement pourra etre declenche quand le participant aura donne son consentement.
                </p>
                <div class="actions">
                  <button id="start-recording-button" class="primary-button" type="button" disabled>Demarrer l'enregistrement</button>
                  <button id="stop-recording-button" class="secondary-button" type="button" disabled>Terminer et envoyer</button>
                </div>
                <div class="recording-meta">
                  <div>
                    <span class="meta-label">Duree</span>
                    <strong id="recording-timer">00:00</strong>
                  </div>
                  <div>
                    <span class="meta-label">Sortie</span>
                    <strong>video participant (mp4) + audio participant/enqueteur (mp3 + wav) + transcription texte</strong>
                  </div>
                </div>
                <p id="recording-status" class="status-line">Connectez la salle puis attendez le participant avant de lancer l'enregistrement.</p>
              </section>
            `
        }
      </div>
    </main>
  `;

  const elements = {
    roomStatus: document.getElementById("room-status"),
    participantConsentCheckbox: document.getElementById("participant-consent-checkbox"),
    consentStatus: document.getElementById("consent-status"),
    connectButton: document.getElementById("connect-button"),
    disconnectButton: document.getElementById("disconnect-button"),
    toggleCameraButton: document.getElementById("toggle-camera-button"),
    toggleMicButton: document.getElementById("toggle-mic-button"),
    startAudioButton: document.getElementById("start-audio-button"),
    connectionStatus: document.getElementById("connection-status"),
    localVideoSlot: document.getElementById("local-video-slot"),
    remoteVideoSlot: document.getElementById("remote-video-slot"),
    localBadge: document.getElementById("local-badge"),
    remoteBadge: document.getElementById("remote-badge"),
    startRecordingButton: document.getElementById("start-recording-button"),
    stopRecordingButton: document.getElementById("stop-recording-button"),
    recordingTimer: document.getElementById("recording-timer"),
    recordingStatus: document.getElementById("recording-status"),
  };

  function setStatus(target, message, tone) {
    if (!target) {
      return;
    }
    target.textContent = message;
    target.dataset.tone = tone || "neutral";
  }

  function getLivekitClient() {
    if (state.livekitClient) {
      return state.livekitClient;
    }
    const candidate = window.LivekitClient || window.LiveKitClient || null;
    if (!candidate) {
      throw new Error("Le client LiveKit local ne s'est pas charge correctement.");
    }
    state.livekitClient = candidate;
    return candidate;
  }

  async function readJsonResponse(response) {
    const rawText = await response.text();
    if (!rawText) {
      return {};
    }
    try {
      return JSON.parse(rawText);
    } catch (error) {
      const preview = rawText.trim().slice(0, 180);
      if (preview.startsWith("<")) {
        throw new Error("Le serveur a renvoye une page HTML au lieu d'une reponse API.");
      }
      throw new Error(preview || "Le serveur a renvoye une reponse invalide.");
    }
  }

  async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const payload = await readJsonResponse(response);
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || payload.message || "Erreur inattendue.");
    }
    return payload;
  }

  function getPrimaryRemoteParticipant() {
    if (!state.room || !state.room.remoteParticipants || !state.room.remoteParticipants.size) {
      return null;
    }
    return Array.from(state.room.remoteParticipants.values())[0] || null;
  }

  function resetNode(container, emptyMessage) {
    if (!container) {
      return;
    }
    container.innerHTML = "";
    container.classList.remove("empty-slot");
    if (emptyMessage) {
      container.textContent = emptyMessage;
      container.classList.add("empty-slot");
    }
  }

  function chooseMimeType(kind) {
    const candidates =
      kind === "participant"
        ? ["video/webm;codecs=vp8,opus", "video/webm", "video/mp4"]
        : ["audio/webm;codecs=opus", "audio/webm", "audio/ogg"];
    for (const candidate of candidates) {
      if (window.MediaRecorder && MediaRecorder.isTypeSupported(candidate)) {
        return candidate;
      }
    }
    return "";
  }

  function createRecorder(stream, mimeType, chunks) {
    const recorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
    recorder.addEventListener("dataavailable", (event) => {
      if (event.data && event.data.size > 0) {
        chunks.push(event.data);
      }
    });
    return recorder;
  }

  function formatTimer(seconds) {
    const safeValue = Number.isFinite(seconds) ? seconds : 0;
    const minutes = String(Math.floor(safeValue / 60)).padStart(2, "0");
    const secs = String(Math.floor(safeValue % 60)).padStart(2, "0");
    return `${minutes}:${secs}`;
  }

  function updateTimer() {
    if (!elements.recordingTimer) {
      return;
    }
    if (!state.startedAt) {
      elements.recordingTimer.textContent = "00:00";
      return;
    }
    const elapsed = Math.floor((Date.now() - state.startedAt.getTime()) / 1000);
    elements.recordingTimer.textContent = formatTimer(elapsed);
  }

  function startTimer() {
    state.startedAt = new Date();
    updateTimer();
    state.timerHandle = window.setInterval(updateTimer, 1000);
  }

  function resetTimer() {
    if (state.timerHandle) {
      window.clearInterval(state.timerHandle);
      state.timerHandle = null;
    }
    state.startedAt = null;
    updateTimer();
  }

  function isRecordingActive() {
    return Boolean(state.participantRecorder || state.investigatorRecorder);
  }

  async function loadCredentials() {
    if (!sessionId) {
      throw new Error("session_id absent dans l'URL.");
    }
    const payload = await fetchJson(
      `/api/livekit/token?session_id=${encodeURIComponent(sessionId)}&role=${encodeURIComponent(role)}`,
    );
    state.credentials = payload;
    return payload;
  }

  async function saveParticipantConsent(consentChecked) {
    return fetchJson("/api/livekit/participant-consent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        consent_checked: Boolean(consentChecked),
      }),
    });
  }

  async function fetchParticipantConsentStatus() {
    return fetchJson(`/api/livekit/participant-consent-status?session_id=${encodeURIComponent(sessionId)}`);
  }

  function getMicrophoneCaptureOptions() {
    return {
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
      channelCount: 1,
    };
  }

  function renderLocalParticipant() {
    resetNode(elements.localVideoSlot);
    if (!state.room) {
      resetNode(elements.localVideoSlot, "Connexion non demarree");
      return;
    }
    const LK = getLivekitClient();
    const cameraPub = state.room.localParticipant.getTrackPublication(LK.Track.Source.Camera);
    if (!cameraPub || !cameraPub.track) {
      resetNode(elements.localVideoSlot, "Camera locale indisponible");
      return;
    }

    const element = cameraPub.track.attach();
    element.className = "live-video-element";
    element.muted = true;
    elements.localVideoSlot.appendChild(element);
  }

  function renderRemoteParticipants() {
    resetNode(elements.remoteVideoSlot);
    const remoteParticipant = getPrimaryRemoteParticipant();
    if (!remoteParticipant) {
      resetNode(elements.remoteVideoSlot, roleCopy.remoteEmpty);
      updateRecordingAvailability();
      return;
    }

    const LK = getLivekitClient();
    const cameraPub = remoteParticipant.getTrackPublication(LK.Track.Source.Camera);
    if (cameraPub && cameraPub.track) {
      const videoElement = cameraPub.track.attach();
      videoElement.className = "live-video-element";
      elements.remoteVideoSlot.appendChild(videoElement);
    } else {
      resetNode(elements.remoteVideoSlot, "Flux video distant indisponible");
    }

    const micPub = remoteParticipant.getTrackPublication(LK.Track.Source.Microphone);
    if (micPub && micPub.track) {
      const audioElement = micPub.track.attach();
      audioElement.autoplay = true;
      audioElement.style.display = "none";
      elements.remoteVideoSlot.appendChild(audioElement);
    }

    updateRecordingAvailability();
  }

  function updateDeviceButtons() {
    const LK = getLivekitClient();
    if (!state.room || !state.connected) {
      elements.toggleCameraButton.disabled = true;
      elements.toggleMicButton.disabled = true;
      elements.startAudioButton.disabled = true;
      elements.disconnectButton.disabled = true;
      return;
    }

    const cameraPub = state.room.localParticipant.getTrackPublication(LK.Track.Source.Camera);
    const micPub = state.room.localParticipant.getTrackPublication(LK.Track.Source.Microphone);
    const cameraEnabled = Boolean(cameraPub && cameraPub.track && !cameraPub.isMuted);
    const micEnabled = Boolean(micPub && micPub.track && !micPub.isMuted);

    elements.toggleCameraButton.disabled = false;
    elements.toggleMicButton.disabled = false;
    elements.startAudioButton.disabled = false;
    elements.disconnectButton.disabled = false;
    elements.toggleCameraButton.textContent = cameraEnabled ? "Camera active" : "Activer camera";
    elements.toggleMicButton.textContent = micEnabled ? "Micro actif" : "Activer micro";
  }

  function updateRecordingAvailability() {
    if (isParticipant || !elements.startRecordingButton) {
      return;
    }

    const remoteParticipant = getPrimaryRemoteParticipant();
    const canStart = state.connected && Boolean(remoteParticipant) && !isRecordingActive() && !state.uploadDone;
    elements.startRecordingButton.disabled = !canStart;
    elements.stopRecordingButton.disabled = !isRecordingActive();

    if (isRecordingActive()) {
      return;
    }
    if (!state.connected) {
      setStatus(elements.recordingStatus, "Connectez d'abord la salle enqueteur.", "neutral");
      return;
    }
    if (!remoteParticipant) {
      setStatus(elements.recordingStatus, "Attendez que le participant rejoigne la salle avant de lancer l'enregistrement.", "neutral");
      return;
    }
    if (state.uploadDone) {
      setStatus(elements.recordingStatus, "L'enregistrement a deja ete transmis pour cette session.", "success");
      return;
    }
    setStatus(elements.recordingStatus, "Tout est pret. Vous pouvez lancer l'enregistrement.", "success");
  }

  function buildParticipantMediaStreamForRecording() {
    const LK = getLivekitClient();
    const remoteParticipant = getPrimaryRemoteParticipant();
    if (!remoteParticipant) {
      throw new Error("Aucun participant distant n'est encore connecte.");
    }

    const stream = new MediaStream();
    const cameraPub = remoteParticipant.getTrackPublication(LK.Track.Source.Camera);
    const micPub = remoteParticipant.getTrackPublication(LK.Track.Source.Microphone);

    if (cameraPub && cameraPub.track && cameraPub.track.mediaStreamTrack) {
      stream.addTrack(cameraPub.track.mediaStreamTrack);
    }
    if (micPub && micPub.track && micPub.track.mediaStreamTrack) {
      stream.addTrack(micPub.track.mediaStreamTrack);
    }

    if (!stream.getTracks().length) {
      throw new Error("Le flux video/audio du participant n'est pas encore disponible.");
    }
    return stream;
  }

  function buildInvestigatorAudioStreamForRecording() {
    const LK = getLivekitClient();
    const stream = new MediaStream();
    const micPub = state.room.localParticipant.getTrackPublication(LK.Track.Source.Microphone);

    if (micPub && micPub.track && micPub.track.mediaStreamTrack) {
      stream.addTrack(micPub.track.mediaStreamTrack);
    }

    if (!stream.getTracks().length) {
      throw new Error("Le micro enqueteur n'est pas disponible pour l'enregistrement.");
    }
    return stream;
  }

  async function connectRoom() {
    if (state.connected || state.connectRequested) {
      return;
    }
    state.connectRequested = true;
    elements.connectButton.disabled = true;
    elements.connectButton.textContent = "Connexion en cours…";
    setStatus(elements.connectionStatus, "Connexion LiveKit en cours…", "neutral");

    try {
      const credentials = state.credentials || (await loadCredentials());
      const LK = getLivekitClient();
      state.room = new LK.Room();

      state.room.on(LK.RoomEvent.TrackSubscribed, () => {
        renderRemoteParticipants();
        updateDeviceButtons();
      });
      state.room.on(LK.RoomEvent.TrackUnsubscribed, () => {
        renderRemoteParticipants();
        updateDeviceButtons();
      });
      state.room.on(LK.RoomEvent.ParticipantConnected, () => {
        renderRemoteParticipants();
        updateDeviceButtons();
      });
      state.room.on(LK.RoomEvent.ParticipantDisconnected, () => {
        renderRemoteParticipants();
        updateDeviceButtons();
      });
      state.room.on(LK.RoomEvent.LocalTrackPublished, () => {
        renderLocalParticipant();
        updateDeviceButtons();
      });
      state.room.on(LK.RoomEvent.LocalTrackUnpublished, () => {
        renderLocalParticipant();
        updateDeviceButtons();
      });
      state.room.on(LK.RoomEvent.Disconnected, () => {
        state.connected = false;
        state.connectRequested = false;
        updateDeviceButtons();
        renderRemoteParticipants();
        renderLocalParticipant();
        updateRecordingAvailability();
        elements.connectButton.disabled = false;
        elements.connectButton.textContent = "Rejoindre la session";
      });

      await state.room.connect(credentials.livekit_url, credentials.token);
      await state.room.localParticipant.setCameraEnabled(true);
      await state.room.localParticipant.setMicrophoneEnabled(true, getMicrophoneCaptureOptions());
      state.connected = true;
      renderLocalParticipant();
      renderRemoteParticipants();
      updateDeviceButtons();
      updateRecordingAvailability();
      setStatus(
        elements.connectionStatus,
        isParticipant
          ? "Session connectee. Vous pouvez echanger avec le chercheur."
          : "Session connectee. Pour une piste enqueteur plus propre, utilisez de preference un casque ou des ecouteurs.",
        "success",
      );
      setStatus(elements.roomStatus, `Salle active : ${credentials.room_name}`, "success");

      if (isParticipant && elements.participantConsentCheckbox && elements.participantConsentCheckbox.checked) {
        await saveParticipantConsent(true);
      }
    } catch (error) {
      state.connectRequested = false;
      state.connected = false;
      elements.connectButton.disabled = false;
      elements.connectButton.textContent = "Rejoindre la session";
      setStatus(elements.connectionStatus, error.message || "Connexion LiveKit impossible.", "error");
      setStatus(elements.roomStatus, "Impossible de charger la salle LiveKit.", "error");
    }
  }

  async function disconnectRoom() {
    if (state.room) {
      state.room.disconnect();
      state.room = null;
    }
    state.connected = false;
    state.connectRequested = false;
    elements.connectButton.disabled = false;
    elements.connectButton.textContent = "Rejoindre la session";
    resetNode(elements.localVideoSlot, "Connexion non demarree");
    resetNode(elements.remoteVideoSlot, roleCopy.remoteEmpty);
    updateDeviceButtons();
    updateRecordingAvailability();
    setStatus(elements.connectionStatus, "Session LiveKit deconnectee.", "neutral");
    setStatus(elements.roomStatus, roleCopy.roomHint, "neutral");
  }

  async function toggleCamera() {
    if (!state.room) {
      return;
    }
    const LK = getLivekitClient();
    const cameraPub = state.room.localParticipant.getTrackPublication(LK.Track.Source.Camera);
    const shouldEnable = !(cameraPub && cameraPub.track && !cameraPub.isMuted);
    await state.room.localParticipant.setCameraEnabled(shouldEnable);
    renderLocalParticipant();
    updateDeviceButtons();
  }

  async function toggleMicrophone() {
    if (!state.room) {
      return;
    }
    const LK = getLivekitClient();
    const micPub = state.room.localParticipant.getTrackPublication(LK.Track.Source.Microphone);
    const shouldEnable = !(micPub && micPub.track && !micPub.isMuted);
    await state.room.localParticipant.setMicrophoneEnabled(shouldEnable, getMicrophoneCaptureOptions());
    updateDeviceButtons();
  }

  async function unlockAudio() {
    const audioElements = elements.remoteVideoSlot.querySelectorAll("audio");
    let unlocked = false;
    for (const audioElement of audioElements) {
      try {
        await audioElement.play();
        unlocked = true;
      } catch (error) {
        // ignored on purpose
      }
    }
    setStatus(
      elements.connectionStatus,
      unlocked ? "Lecture audio autorisee." : "Aucun flux audio distant n'est encore disponible.",
      unlocked ? "success" : "neutral",
    );
  }

  async function startRecording() {
    try {
      const consentStatus = await fetchParticipantConsentStatus();
      if (!consentStatus.consent_checked) {
        throw new Error("Le participant doit d'abord cocher le consentement avant le demarrage.");
      }

      const participantStream = buildParticipantMediaStreamForRecording();
      const investigatorStream = buildInvestigatorAudioStreamForRecording();

      state.participantChunks = [];
      state.investigatorChunks = [];

      const participantMimeType = chooseMimeType("participant");
      const investigatorMimeType = chooseMimeType("investigator");

      state.participantRecorder = createRecorder(participantStream, participantMimeType, state.participantChunks);
      state.investigatorRecorder = createRecorder(investigatorStream, investigatorMimeType, state.investigatorChunks);

      state.participantRecorder.start(1000);
      state.investigatorRecorder.start(1000);

      startTimer();
      elements.startRecordingButton.disabled = true;
      elements.stopRecordingButton.disabled = false;
      setStatus(
        elements.recordingStatus,
        "Enregistrement en cours : video du participant, audio du participant et audio de l'enqueteur.",
        "recording",
      );
    } catch (error) {
      state.participantRecorder = null;
      state.investigatorRecorder = null;
      setStatus(elements.recordingStatus, error.message || "Impossible de demarrer l'enregistrement.", "error");
      updateRecordingAvailability();
    }
  }

  async function stopRecorder(recorder) {
    if (!recorder) {
      return;
    }
    const stopped = new Promise((resolve) => recorder.addEventListener("stop", resolve, { once: true }));
    recorder.stop();
    await stopped;
  }

  async function uploadBlob(endpoint, blob, mimeType, filename, recordingStartedAt, recordingEndedAt) {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Content-Type": mimeType,
        "X-Consent-Checked": "true",
        "X-Client-Timezone": Intl.DateTimeFormat().resolvedOptions().timeZone || "",
        "X-Recording-Started-At": recordingStartedAt,
        "X-Recording-Ended-At": recordingEndedAt,
        "X-Original-Filename": filename,
      },
      body: blob,
    });
    const payload = await readJsonResponse(response);
    if (!response.ok || !payload.ok) {
      throw new Error(payload.error || "L'envoi a echoue.");
    }
    return payload;
  }

  async function stopRecording() {
    if (!isRecordingActive()) {
      return;
    }

    const participantRecorder = state.participantRecorder;
    const investigatorRecorder = state.investigatorRecorder;
    const recordingStartedAt = state.startedAt ? state.startedAt.toISOString() : "";

    await Promise.all([stopRecorder(participantRecorder), stopRecorder(investigatorRecorder)]);

    resetTimer();
    state.participantRecorder = null;
    state.investigatorRecorder = null;
    elements.stopRecordingButton.disabled = true;

    try {
      setStatus(elements.recordingStatus, "Envoi des fichiers vers l'ordinateur du chercheur…", "neutral");

      const participantMimeType = participantRecorder.mimeType || chooseMimeType("participant") || "video/webm";
      const participantExtension = participantMimeType.includes("mp4") ? "mp4" : "webm";
      const participantBlob = new Blob(state.participantChunks, { type: participantMimeType });

      const investigatorMimeType = investigatorRecorder.mimeType || chooseMimeType("investigator") || "audio/webm";
      const investigatorExtension = investigatorMimeType.includes("ogg") ? "ogg" : "webm";
      const investigatorBlob = new Blob(state.investigatorChunks, { type: investigatorMimeType });

      const endedAt = new Date().toISOString();

      await uploadBlob(
        `/api/livekit/upload-participant-recording?session_id=${encodeURIComponent(sessionId)}`,
        participantBlob,
        participantMimeType,
        `participant.${participantExtension}`,
        recordingStartedAt,
        endedAt,
      );

      await uploadBlob(
        `/api/livekit/upload-investigator-audio?session_id=${encodeURIComponent(sessionId)}`,
        investigatorBlob,
        investigatorMimeType,
        `investigator.${investigatorExtension}`,
        recordingStartedAt,
        endedAt,
      );

      state.uploadDone = true;
      setStatus(
        elements.recordingStatus,
        "Enregistrement termine et transmis. La transcription Whisper continue maintenant sur le poste du chercheur.",
        "success",
      );
    } catch (error) {
      setStatus(elements.recordingStatus, error.message || "L'envoi a echoue.", "error");
    } finally {
      updateRecordingAvailability();
    }
  }

  async function loadConsentState() {
    if (!isParticipant || !elements.participantConsentCheckbox) {
      return;
    }
    try {
      const payload = await fetchParticipantConsentStatus();
      elements.participantConsentCheckbox.checked = Boolean(payload.consent_checked);
      setStatus(
        elements.consentStatus,
        payload.consent_checked
          ? "Consentement enregistre. Le chercheur peut maintenant lancer l'enregistrement."
          : "Le consentement n'est pas encore donne.",
        payload.consent_checked ? "success" : "neutral",
      );
    } catch (error) {
      setStatus(elements.consentStatus, error.message || "Impossible de recuperer le consentement.", "error");
    }
  }

  function bindEvents() {
    elements.connectButton.addEventListener("click", connectRoom);
    elements.disconnectButton.addEventListener("click", disconnectRoom);
    elements.toggleCameraButton.addEventListener("click", toggleCamera);
    elements.toggleMicButton.addEventListener("click", toggleMicrophone);
    elements.startAudioButton.addEventListener("click", unlockAudio);

    if (elements.startRecordingButton) {
      elements.startRecordingButton.addEventListener("click", startRecording);
    }
    if (elements.stopRecordingButton) {
      elements.stopRecordingButton.addEventListener("click", stopRecording);
    }

    if (elements.participantConsentCheckbox) {
      elements.participantConsentCheckbox.addEventListener("change", async () => {
        try {
          const consentChecked = elements.participantConsentCheckbox.checked;
          await saveParticipantConsent(consentChecked);
          setStatus(
            elements.consentStatus,
            consentChecked
              ? "Consentement enregistre. Le chercheur peut maintenant lancer l'enregistrement."
              : "Consentement retire.",
            consentChecked ? "success" : "neutral",
          );
        } catch (error) {
          setStatus(elements.consentStatus, error.message || "Impossible d'enregistrer le consentement.", "error");
        }
      });
    }

    window.addEventListener("beforeunload", () => {
      if (state.room) {
        state.room.disconnect();
      }
      resetTimer();
    });
  }

  async function bootstrap() {
    try {
      getLivekitClient();
    } catch (error) {
      setStatus(elements.connectionStatus, error.message || "Le client LiveKit local est indisponible.", "error");
      setStatus(elements.roomStatus, "Impossible de charger le client LiveKit.", "error");
      elements.connectButton.disabled = true;
      return;
    }

    bindEvents();
    updateDeviceButtons();
    updateRecordingAvailability();

    try {
      const credentials = await loadCredentials();
      setStatus(elements.roomStatus, `Salle prete : ${credentials.room_name}`, "success");
      setStatus(elements.connectionStatus, roleCopy.connectionHint, "neutral");
      elements.connectButton.disabled = false;
    } catch (error) {
      setStatus(elements.roomStatus, error.message || "Impossible de charger la session LiveKit.", "error");
      setStatus(elements.connectionStatus, error.message || "Impossible de charger la session LiveKit.", "error");
      elements.connectButton.disabled = true;
    }

    await loadConsentState();
  }

  bootstrap();
})();
