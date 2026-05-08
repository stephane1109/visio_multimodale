(function () {
  const elements = {
    helpNgrokSteps: document.getElementById("help-ngrok-steps"),
    helpFfmpegSteps: document.getElementById("help-ffmpeg-steps"),
  };

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function getNgrokProcedure() {
    return [
      {
        title: "1. Créer le compte ngrok",
        description: "Créer un compte ngrok puis récupérer l'authtoken dans le tableau de bord.",
        command: "https://dashboard.ngrok.com/get-started/your-authtoken",
      },
      {
        title: "2. Installer ngrok sur Mac",
        description: "Si Homebrew est déjà installé, la commande la plus simple est celle-ci.",
        command: "brew install ngrok",
      },
      {
        title: "3. Enregistrer l'authtoken",
        description: "Dans l'application, collez l'authtoken puis cliquez sur \"Enregistrer l'authtoke\".",
        command: "Champ : Authtoken ngrok",
      },
      {
        title: "4. Démarrer l'accès distant",
        description: "Cliquer sur \"Démarrer l'accès distant\" dans l'application.",
        command: "Bouton : Démarrer l'accès distant",
      },
      {
        title: "5. Installer ngrok sur Windows",
        description: "Télécharger l'archive Windows officielle puis la décompresser dans un dossier simple, par exemple \"C:\\ngrok\".",
        command: "https://ngrok.com/downloads/windows",
      },
      {
        title: "6. Ajouter ngrok au PATH sur Windows",
        description: "Ajoutez `ngrok.exe` au PATH Windows pour que l'application puisse le lancer directement.",
        command: "Ajouter ngrok.exe au PATH Windows",
      },
    ];
  }

  function getFfmpegProcedure() {
    return [
      {
        title: "Installer FFmpeg sur Mac avec Homebrew",
        description: "La méthode la plus simple sur Mac est d'utiliser Homebrew.",
        command: "brew install ffmpeg",
      },
      {
        title: "Alternative officielle Mac",
        description: "La page officielle FFmpeg référence aussi les options de téléchargement et de compilation.",
        command: "https://ffmpeg.org/download.html#build-macos",
      },
      {
        title: "Vérifier l'installation sur Mac",
        description: "Ouvrir le Terminal puis vérifier que la commande répond.",
        command: "ffmpeg -version",
      },
      {
        title: "Télécharger FFmpeg pour Windows",
        description: "Ouvrir la page officielle FFmpeg. Elle renvoie vers des builds Windows recommandés par le projet.",
        command: "https://ffmpeg.org/download.html#build-windows",
      },
      {
        title: "Décompresser l'archive Windows",
        description: "Extraire l'archive téléchargée puis repérer le dossier `bin` qui contient `ffmpeg.exe`.",
        command: "C:\\ffmpeg\\bin\\ffmpeg.exe",
      },
      {
        title: "Ajouter FFmpeg au PATH sur Windows",
        description: "Ajouter le dossier `bin` aux variables d'environnement Windows pour que `ffmpeg` soit trouvé partout.",
        command: "Ajouter C:\\ffmpeg\\bin au PATH Windows",
      },
      {
        title: "Vérifier l'installation sur Windows",
        description: "Ouvrir PowerShell ou l'invite de commandes puis vérifier que la commande répond.",
        command: "ffmpeg -version",
      },
    ];
  }

  function renderCommandCard(item) {
    const isUrl = String(item.command || "").startsWith("http");
    const commandBlock = isUrl
      ? `<p class="mono-block"><a href="${escapeHtml(item.command)}" target="_blank" rel="noreferrer">${escapeHtml(item.command)}</a></p>`
      : `<p class="mono-block">${escapeHtml(item.command)}</p>`;

    return `
      <article class="item-card command-card">
        <div class="item-head">
          <div>
            <strong>${escapeHtml(item.title)}</strong>
            <p class="mini-line">${escapeHtml(item.description)}</p>
          </div>
        </div>
        ${commandBlock}
      </article>
    `;
  }

  function renderHelpPage() {
    elements.helpNgrokSteps.innerHTML = getNgrokProcedure().map(renderCommandCard).join("");
    elements.helpFfmpegSteps.innerHTML = getFfmpegProcedure().map(renderCommandCard).join("");
  }

  renderHelpPage();
})();
