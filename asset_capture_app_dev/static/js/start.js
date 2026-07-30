(function () {
    const qrReaderElem = document.getElementById("qr-reader");
    const startBtn = document.getElementById("startScanner");
    const stopBtn = document.getElementById("stopScanner");
    const torchBtn = document.getElementById("torchToggle");
    const qrStatus = document.getElementById("qr_status");
    const qrInput = document.getElementById("qr_code");
    const overwriteInput = document.getElementById("overwrite");
    const form = document.getElementById("startForm");
    const submitBtn = document.getElementById("submitBtn");
    const manual = document.getElementById("manual_qr");
    const useManualBtn = document.getElementById("useManual");
    const inlineAlert = document.getElementById("qr_inline_alert");

    // Offline Banner Handling
    let offlineBanner = document.getElementById('offline-banner');
    if (!offlineBanner) {
        offlineBanner = document.createElement('div');
        offlineBanner.id = 'offline-banner';
        offlineBanner.style.cssText = 'display:none; background-color:#ef4444; color:white; text-align:center; padding:8px; font-weight:bold; position:fixed; top:0; left:0; right:0; z-index:9999;';
        offlineBanner.textContent = 'You are offline. Some features may be limited.';
        document.body.prepend(offlineBanner);
    }

    function updateOnlineStatus() {
        if (navigator.onLine) {
            offlineBanner.style.display = 'none';
            if (startBtn) startBtn.disabled = false;
        } else {
            offlineBanner.style.display = 'block';
            // Depending on requirements, we might disable scanning if it relies on API
            // But scanning itself is local. CheckExists is API.
            // We'll handle API failures gracefully instead of disabling everything.
        }
    }
    window.addEventListener('online', updateOnlineStatus);
    window.addEventListener('offline', updateOnlineStatus);
    updateOnlineStatus(); // Initial check

    const placeholderMarkup = `
      <div id="qr-placeholder" class="qr-placeholder">
        <img src="/static/img/scan_qr_icon.svg" alt="QR scan illustration"
          class="qr-icon" style="width: 80px; height: auto; opacity: 0.9;" loading="lazy">
        <div class="scanner-viewfinder">
          <div class="scanner-corner scanner-corner--tl"></div>
          <div class="scanner-corner scanner-corner--tr"></div>
          <div class="scanner-corner scanner-corner--bl"></div>
          <div class="scanner-corner scanner-corner--br"></div>
        </div>
      </div>`;

    let html5QrCode = null;
    let scanning = false;
    let lastScanSuccess = false;
    const vibratePattern = [50, 30, 50];
    let audioCtx = null;
    const beepAudioSrc = "data:audio/wav;base64,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";
    let beepAudioEl = null;
    let torchSupported = false;
    let torchEnabled = false;

    function hapticSuccess() {
        try {
            if (navigator.vibrate) {
                const ok = navigator.vibrate(vibratePattern);
                if (ok === false) navigator.vibrate(80);
            }
        } catch (e) {
            console.warn("Vibration not available:", e);
        }
    }

    function resumeAudioCtx() {
        try {
            if (!audioCtx) {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                if (!AudioContext) return;
                audioCtx = new AudioContext();
            }
            if (audioCtx.state === "suspended") audioCtx.resume();
        } catch (e) {
            console.warn("AudioContext unavailable:", e);
        }
    }

    function ensureBeepAudio() {
        if (!beepAudioEl && beepAudioSrc) {
            beepAudioEl = new Audio(beepAudioSrc);
            beepAudioEl.preload = "auto";
            beepAudioEl.setAttribute("playsinline", "true");
        }
    }

    // Some browsers (Edge/Chromium) need a user-gesture warmup to allow playback.
    function primeBeepAudio() {
        try {
            ensureBeepAudio();
            if (!beepAudioEl) return;
            beepAudioEl.muted = true;
            beepAudioEl.currentTime = 0;
            const p = beepAudioEl.play();
            if (p && p.catch) p.catch(() => { });
            setTimeout(() => {
                try {
                    beepAudioEl.pause();
                    beepAudioEl.currentTime = 0;
                    beepAudioEl.muted = false;
                } catch { }
            }, 60);
        } catch (e) {
            console.warn("Prime audio failed:", e);
        }
    }

    function beepSuccess() {
        try {
            resumeAudioCtx();
            ensureBeepAudio();
            if (audioCtx) {
                const now = audioCtx.currentTime;
                const makeBeep = (startTime) => {
                    const osc = audioCtx.createOscillator();
                    const gain = audioCtx.createGain();
                    osc.type = "sine";
                    osc.frequency.setValueAtTime(1000, startTime);
                    gain.gain.setValueAtTime(0.0001, startTime);
                    gain.gain.exponentialRampToValueAtTime(0.5, startTime + 0.02);
                    gain.gain.exponentialRampToValueAtTime(0.0001, startTime + 0.36);
                    osc.connect(gain).connect(audioCtx.destination);
                    osc.start(startTime);
                    osc.stop(startTime + 0.36);
                };
                makeBeep(now);
                makeBeep(now + 0.48);
            } else if (beepAudioSrc) {
                const playOnce = () => {
                    try {
                        const a = new Audio(beepAudioSrc);
                        a.volume = 1.0;
                        a.play().catch(() => { });
                    } catch { }
                };
                playOnce();
                setTimeout(playOnce, 480);
            }
        } catch (e) {
            console.warn("Beep failed:", e);
        }
    }

    function disableTorchUI() {
        torchSupported = false;
        torchEnabled = false;
        torchBtn.disabled = true;
        torchBtn.classList.add("btn-outline");
        torchBtn.textContent = "Torch";
        torchBtn.title = "Torch not available";
    }

    async function syncTorchSupport() {
        if (!html5QrCode || typeof html5QrCode.getRunningTrackCapabilities !== "function") {
            disableTorchUI();
            return;
        }
        try {
            const caps = await html5QrCode.getRunningTrackCapabilities();
            torchSupported = !!(caps && caps.torch);
            if (torchSupported) {
                torchBtn.disabled = false;
                torchBtn.title = "Toggle flashlight";
            } else {
                disableTorchUI();
            }
        } catch (e) {
            console.warn("Torch capability check failed:", e);
            disableTorchUI();
        }
    }

    async function applyTorchState() {
        if (!torchSupported || !html5QrCode || typeof html5QrCode.applyVideoConstraints !== "function") return;
        try {
            await html5QrCode.applyVideoConstraints({ advanced: [{ torch: torchEnabled }] });
            torchBtn.textContent = torchEnabled ? "Torch On" : "Torch";
            torchBtn.classList.toggle("btn-success", torchEnabled);
            torchBtn.classList.toggle("btn-outline", !torchEnabled);
        } catch (e) {
            console.warn("Torch toggle failed:", e);
            disableTorchUI();
        }
    }

    function applyPlaceholderSuccessState() {
        const placeholder = document.getElementById("qr-placeholder");
        if (!placeholder) return;
        placeholder.classList.toggle("is-success", !!lastScanSuccess);
    }

    function restorePlaceholder(forceSuccess = null) {
        qrReaderElem.innerHTML = placeholderMarkup;
        qrReaderElem.style.backgroundColor = "#e5e7eb";
        if (forceSuccess !== null) lastScanSuccess = forceSuccess;
        applyPlaceholderSuccessState();
    }

    function togglePlaceholder(show) {
        const placeholder = document.getElementById("qr-placeholder");
        if (placeholder) placeholder.style.display = show ? "flex" : "none";
    }

    function addLiveScanLine() {
        removeLiveScanLine();
        const line = document.createElement("div");
        line.id = "scan-line-live";
        line.className = "scan-line scan-line-live";
        qrReaderElem.appendChild(line);
    }

    function removeLiveScanLine() {
        const line = document.getElementById("scan-line-live");
        if (line && line.parentNode) line.parentNode.removeChild(line);
    }

    function clearInlineAlert() { inlineAlert.innerHTML = ""; }

    function showInlineAlert(options) {
        inlineAlert.innerHTML = `
        <div class="inline-alert" style="color:#0f172a;">
          <div class="alert-message" style="font-weight:600;">${options.message}</div>
          <div class="alert-actions">
            <button type="button" id="alertContinue" class="btn btn-warning">
              Continue & replace
            </button>
            <button type="button" id="alertCancel" class="btn btn-outline">
              Scan another
            </button>
          </div>
        </div>`;
        document.getElementById("alertContinue").onclick = options.onContinue;
        document.getElementById("alertCancel").onclick = options.onCancel;
    }

    // Show parameter change comparison alert
    function showParameterChangeAlert(options) {
        const { currentParams, newParams, onUpdateParams, onKeepExisting, onCancel } = options;

        // Build comparison table
        let changesHtml = '<table class="param-compare-table" style="width:100%; margin: 10px 0; font-size: 0.9em;">';
        changesHtml += '<tr><th style="text-align:left;padding:4px;">Parameter</th><th style="text-align:left;padding:4px;">Current</th><th style="text-align:left;padding:4px;">New</th></tr>';

        if (currentParams.building !== newParams.building) {
            changesHtml += `<tr style="background:#fff3cd;"><td style="padding:4px;">Building</td><td style="padding:4px;">${currentParams.building || 'N/A'}</td><td style="padding:4px;"><strong>${newParams.building}</strong></td></tr>`;
        }
        if (currentParams.location !== newParams.location) {
            changesHtml += `<tr style="background:#fff3cd;"><td style="padding:4px;">Location</td><td style="padding:4px;">${currentParams.location || 'N/A'}</td><td style="padding:4px;"><strong>${newParams.location}</strong></td></tr>`;
        }
        if (currentParams.assetType !== newParams.assetType) {
            changesHtml += `<tr style="background:#fff3cd;"><td style="padding:4px;">Asset Type</td><td style="padding:4px;">${currentParams.assetType || 'N/A'}</td><td style="padding:4px;"><strong>${newParams.assetType}</strong></td></tr>`;
        }
        changesHtml += '</table>';

        inlineAlert.innerHTML = `
        <div class="inline-alert" style="background:#f8f9fa; border:1px solid #dee2e6; border-radius:8px; padding:16px; margin-top:12px; color:#0f172a;">
          <div class="alert-message" style="font-weight:700; color:#0f172a;">⚠️ Location Mismatch Detected</div>
          <div style="margin-top:8px; color:#111827; font-size:0.65em;">This QR code was previously captured with different Location:</div>
          ${changesHtml}
          <div style="margin-top:12px; color:#6c757d; font-size:0.64em;">
            <strong>Update Location:</strong> seting new location for the asset<br>
            <strong>Keep Existing:</strong> current asset location (no changes).
          </div>
          <div class="alert-actions" style="display:flex; gap:8px; margin-top:12px; flex-wrap:wrap;">
            <button type="button" id="alertUpdateParams" class="btn btn-primary" style="flex:1;">
              Update Location
            </button>
            <button type="button" id="alertKeepExisting" class="btn btn-warning" style="flex:1;">
              Keep Existing
            </button>
            <button type="button" id="alertCancelScan" class="btn btn-outline" style="flex:1;">
              Scan Another
            </button>
          </div>
        </div>`;
        document.getElementById("alertUpdateParams").onclick = onUpdateParams;
        document.getElementById("alertKeepExisting").onclick = onKeepExisting;
        document.getElementById("alertCancelScan").onclick = onCancel;
    }

    function extractNumericId(text) {
        if (!text) return "";
        const matches = String(text).match(/\d{6,}/g);
        if (!matches) return "";
        let best = matches[0];
        for (const m of matches) if (m.length >= best.length) best = m;
        return best;
    }

    async function checkExists(numericId) {
        try {
            const res = await fetch(`/api_check_qr?qr=${encodeURIComponent(numericId)}`);
            const data = await res.json();
            return data; // Return full response including current params
        } catch (err) {
            console.warn("QR existence check failed:", err);
            // Offline Handling:
            // If fetch fails, we assume it's offline or API error.
            // We should warn the user.
            if (!navigator.onLine) {
                alert("Unable to check asset existence while offline.");
                return { exists: false, offline: true };
            }
            return { exists: false };
        }
    }

    // Map asset type abbreviation to full name
    function abbrevToAssetType(abbrev) {
        const map = { 'ME': 'Mechanical', 'EL': 'Electrical', 'BF': 'Backflow' };
        return map[abbrev] || abbrev;
    }

    // Map asset type to abbreviation
    function assetTypeToAbbrev(type) {
        const t = (type || '').toLowerCase();
        if (t.startsWith('mech')) return 'ME';
        if (t.startsWith('elec')) return 'EL';
        if (t.startsWith('back')) return 'BF';
        return type.substring(0, 2).toUpperCase();
    }

    // Call API to update parameters
    async function updateParameters(qrCode, oldParams, newParams) {
        try {
            const res = await fetch('/api_update_parameters', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    qr_code: qrCode,
                    old_params: oldParams,
                    new_params: newParams
                })
            });
            const data = await res.json();
            return data;
        } catch (err) {
            console.error("Parameter update failed:", err);
            return { success: false, error: err.message };
        }
    }

    async function handleExistence(numericId) {
        overwriteInput.value = "0";
        const data = await checkExists(numericId);

        if (data.offline) return true; // Treat as success to allow scanning, but maybe warn?
        if (!data.exists) {
            clearInlineAlert();
            qrStatus.className = "qr-status success";
            return true;
        }

        // Get current form values
        const newBuilding = document.getElementById('building_code').value;
        const newLocation = document.getElementById('location').value;
        const newAssetType = document.getElementById('asset_type').value;

        // Check if parameters differ
        const currentBuilding = data.current_building || '';
        const currentLocation = data.current_location || '';
        const currentAssetType = data.current_asset_type || '';

        const buildingChanged = !!newBuilding && currentBuilding !== newBuilding;
        const locationChanged = !!newLocation && currentLocation !== newLocation;
        const assetTypeChanged = !!newAssetType && assetTypeToAbbrev(newAssetType) !== (currentAssetType || "");

        const paramsChanged = buildingChanged || locationChanged || assetTypeChanged;

        if (paramsChanged) {
            // Show parameter change dialog
            qrStatus.className = "qr-status";
            qrStatus.textContent = "⚠️ Location differ from existing record.";

            showParameterChangeAlert({
                currentParams: {
                    building: currentBuilding,
                    location: currentLocation,
                    assetType: abbrevToAssetType(currentAssetType)
                },
                newParams: {
                    building: newBuilding,
                    location: newLocation,
                    assetType: newAssetType
                },
                onUpdateParams: async () => {
                    // Call update API
                    qrStatus.textContent = "⏳ Updating parameters...";
                    qrStatus.className = "qr-status";

                    const result = await updateParameters(numericId, {
                        building_code: currentBuilding,
                        location: currentLocation,
                        asset_type: abbrevToAssetType(currentAssetType)
                    }, {
                        building_code: newBuilding,
                        location: newLocation,
                        asset_type: newAssetType
                    });

                    if (result.success) {
                        overwriteInput.value = "1";
                        qrStatus.textContent = "✅ " + result.message;
                        qrStatus.className = "qr-status success";
                        clearInlineAlert();
                    } else {
                        qrStatus.textContent = "❌ Update failed: " + result.error;
                        qrStatus.className = "qr-status error";
                    }
                },
                onKeepExisting: () => {
                    // Continue with existing params (update form to match DB)
                    document.getElementById('building_code').value = currentBuilding;
                    document.getElementById('location').innerHTML = `<option value="${currentLocation}" selected>${currentLocation}</option>`;
                    document.getElementById('asset_type').value = abbrevToAssetType(currentAssetType);
                    if (window.setAssetTypeIcon) {
                        window.setAssetTypeIcon(document.getElementById('asset_type').value);
                    }

                    overwriteInput.value = "1";
                    qrStatus.textContent = "✅ Using existing parameters. Ready to replace photos.";
                    qrStatus.className = "qr-status success";
                    clearInlineAlert();
                },
                onCancel: () => {
                    qrInput.value = "";
                    qrStatus.textContent = "QR cleared. Please scan another code.";
                    qrStatus.className = "qr-status";
                    clearInlineAlert();
                }
            });
            return false;
        }

        // No parameter changes, just show standard overwrite dialog
        qrStatus.className = "qr-status";
        showInlineAlert({
            message: "⚠️ This Asset already exists. Would you like to continue and replace existing data?",
            onContinue: () => {
                overwriteInput.value = "1";
                qrStatus.textContent = "✅ Overwrite enabled. You will replace existing data for this asset.";
                qrStatus.className = "qr-status success";
                clearInlineAlert();
            },
            onCancel: () => {
                qrInput.value = "";
                qrStatus.textContent = "QR cleared. Please scan another code.";
                qrStatus.className = "qr-status";
                clearInlineAlert();
            }
        });
        return false;
    }

    function libraryReady() { return typeof Html5Qrcode !== "undefined"; }

    async function setQrValueAndCheck(raw) {
        clearInlineAlert();
        lastScanSuccess = false;
        applyPlaceholderSuccessState();

        // Strict Manual Entry Validation handled at call site for manual entry.
        // For scanner, we still use regex/extraction.

        const id = extractNumericId(raw) || (raw || "").trim();
        if (!id) {
            qrInput.value = "";
            qrStatus.textContent = "⚠️ Could not extract a numeric ID. Try again.";
            qrStatus.className = "qr-status error";
            return false;
        }
        qrInput.value = id;
        qrStatus.textContent = "✅ QR captured: " + id;
        qrStatus.className = "qr-status success";
        lastScanSuccess = true;
        applyPlaceholderSuccessState();
        hapticSuccess();
        beepSuccess();
        await handleExistence(id);

        // Record recent scan
        const buildingSelect = document.getElementById('building_code');
        const buildingCode = buildingSelect.value;
        const buildingName = buildingSelect.options[buildingSelect.selectedIndex]?.text || buildingCode;
        const locationVal = document.getElementById('location').value;
        const assetTypeVal = document.getElementById('asset_type').value;
        if (window.assetRecentScans && typeof window.assetRecentScans.add === 'function') {
            window.assetRecentScans.add({
                qr: id,
                building: buildingCode,
                buildingName: buildingName,
                location: locationVal,
                assetType: assetTypeVal
            });
        }
        return true;
    }

    // Persistent Camera Permission Check
    async function startCamera() {
        if (!libraryReady()) {
            qrStatus.textContent = "⚠️ QR library failed to load.";
            qrStatus.className = "qr-status error";
            return;
        }

        // Check permission state (if supported)
        if (navigator.permissions && navigator.permissions.query) {
            try {
                const status = await navigator.permissions.query({ name: 'camera' });
                if (status.state === 'denied') {
                    qrStatus.textContent = "⚠️ Camera access denied. Please reset permissions in browser settings.";
                    qrStatus.className = "qr-status error";
                    return;
                }
            } catch (e) { /* ignore, optional feature */ }
        }

        if (!html5QrCode) html5QrCode = new Html5Qrcode("qr-reader");

        lastScanSuccess = false;
        applyPlaceholderSuccessState();
        resumeAudioCtx();
        primeBeepAudio();
        ensureBeepAudio();
        togglePlaceholder(false);

        try {
            await html5QrCode.start(
                { facingMode: { exact: "environment" } },
                { fps: 10, qrbox: 200 },
                async (decodedText) => {
                    const success = await setQrValueAndCheck(decodedText);
                    html5QrCode.stop().then(() => {
                        scanning = false;
                        removeLiveScanLine();
                        restorePlaceholder(success);
                        torchEnabled = false;
                        disableTorchUI();
                    });
                },
                () => { }
            );
            addLiveScanLine();
            await syncTorchSupport();

            scanning = true;
            qrStatus.textContent = "📷 Scanning... hold the QR steadily.";
            qrStatus.className = "qr-status";
        } catch (err) {
            qrStatus.textContent = "⚠️ Unable to access camera: " + err;
            qrStatus.className = "qr-status error";
            lastScanSuccess = false;
            removeLiveScanLine();
            torchEnabled = false;
            disableTorchUI();
            restorePlaceholder(false);
        }
    }

    startBtn.addEventListener("click", startCamera);

    stopBtn.addEventListener("click", function () {
        if (html5QrCode && scanning) {
            html5QrCode.stop().then(() => {
                scanning = false;
                qrStatus.textContent = "Scanner stopped.";
                qrStatus.className = "qr-status";
                clearInlineAlert();
                removeLiveScanLine();
                torchEnabled = false;
                disableTorchUI();
                restorePlaceholder();
            });
        }
    });

    useManualBtn.addEventListener("click", async function () {
        const val = manual.value.trim();
        if (!val) { alert("Please type a QR value first."); return; }

        // Mandatory Requirement: Numeric Only AND Exactly 10 digits
        if (!/^\d{10}$/.test(val)) {
            alert("Invalid QR Code.\n\nRequirements:\n1. Only numbers allowed.\n2. Must be exactly 10 digits.\nExample: 0000184952");
            return;
        }

        await setQrValueAndCheck(val);
    });

    torchBtn.addEventListener("click", async function () {
        if (!torchSupported) return;
        torchEnabled = !torchEnabled;
        await applyTorchState();
    });

    form.addEventListener("submit", async function (e) {
        // Validate QR code before adding loading state
        let v = (qrInput.value || "").trim();
        if (!/^\d{6,}$/.test(v)) {
            const id = extractNumericId(v);
            if (id) { qrInput.value = id; v = id; } else {
                e.preventDefault();
                alert("⚠️ Please scan a QR code that contains a numeric ID.");
                return;
            }
        }

        if (overwriteInput.value !== "1") {
            const data = await checkExists(v);
            if (data.exists) {
                e.preventDefault();
                await handleExistence(v);
                return;
            }
        }
    });

    // Check persistent permissions on load
    if (navigator.permissions && navigator.permissions.query) {
        navigator.permissions.query({ name: 'camera' }).then(permissionStatus => {
            if (permissionStatus.state === 'granted') {
                qrStatus.textContent = "Camera permission granted. Auto-starting scanner...";
                // Auto-start since permission is already granted
                startCamera();
            }
        });
    }

})();
