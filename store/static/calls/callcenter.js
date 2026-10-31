/*
 * CallCenter – browser-to-browser voice calls (WebRTC) with Django as the
 * signalling server. Shared by the customer and staff pages.
 */
(function (global) {
  'use strict';

  const POLL_CALL_MS = 700;
  const POLL_INCOMING_MS = 1500;
  const ICE_GATHER_TIMEOUT_MS = 2000;
  const RECONNECT_GRACE_MS = 12000;
  const SCREEN_MAX_BITRATE = 2500000;
  const SCREEN_FPS = 30;

  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  async function api(url, data) {
    const opts = data === undefined
      ? { credentials: 'same-origin', cache: 'no-store' }
      : {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
          body: JSON.stringify(data),
        };
    const res = await fetch(url, opts);
    let body = {};
    try { body = await res.json(); } catch (_) {}
    if (!res.ok) {
      const err = new Error(body.error || 'Request failed (' + res.status + ')');
      err.status = res.status;
      throw err;
    }
    return body;
  }

  function waitForIce(pc) {
    return new Promise((resolve) => {
      if (pc.iceGatheringState === 'complete') return resolve();
      const done = () => { clearTimeout(timer); pc.removeEventListener('icegatheringstatechange', check); resolve(); };
      const check = () => { if (pc.iceGatheringState === 'complete') done(); };
      const timer = setTimeout(done, ICE_GATHER_TIMEOUT_MS);
      pc.addEventListener('icegatheringstatechange', check);
    });
  }

  // Repair connection text (SDP) that the server or database has altered. It
  // must use CRLF line endings and END with one: a server that trims whitespace
  // (.strip(), a Django form field) removes the final CRLF and the browser then
  // fails with "Failed to parse SessionDescription ... Invalid SDP line".
  function fixSdp(sdp) {
    if (sdp && typeof sdp === 'object') sdp = sdp.sdp;   // {type, sdp} stored as an object
    return String(sdp || '')
      .replace(/\\r\\n|\\n/g, '\n')                    // literal "\r\n" from double-escaped JSON
      .replace(/\r?\n/g, '\r\n')
      .replace(/^\s+/, '')
      .replace(/\s*$/, '\r\n');
  }

  function tuneOpus(sdp) {
    if (!sdp) return '';
    const rtpmap = sdp.match(/a=rtpmap:(\d+) opus\/48000\/2/i);
    if (!rtpmap) return sdp;
    const pt = rtpmap[1];
    const params = 'minptime=10;useinbandfec=1;usedtx=0;stereo=0;sprop-stereo=0;' +
                   'maxaveragebitrate=48000;maxplaybackrate=48000';
    const fmtp = new RegExp('a=fmtp:' + pt + ' [^\\r\\n]*');
    return fmtp.test(sdp)
      ? sdp.replace(fmtp, 'a=fmtp:' + pt + ' ' + params)
      : sdp.replace(rtpmap[0], rtpmap[0] + '\r\na=fmtp:' + pt + ' ' + params);
  }

  function preferOpus(pc) {
    if (!global.RTCRtpReceiver || !RTCRtpReceiver.getCapabilities) return;
    const caps = RTCRtpReceiver.getCapabilities('audio');
    if (!caps) return;
    const opus = caps.codecs.filter(c => /opus/i.test(c.mimeType));
    const rest = caps.codecs.filter(c => !/opus/i.test(c.mimeType));
    pc.getTransceivers().forEach((t) => {
      if (t.receiver.track.kind === 'audio' && t.setCodecPreferences) {
        try { t.setCodecPreferences(opus.concat(rest)); } catch (_) {}
      }
    });
  }

  async function tuneSender(pc) {
    for (const sender of pc.getSenders()) {
      if (!sender.track || sender.track.kind !== 'audio') continue;
      try {
        const p = sender.getParameters();
        if (!p.encodings || !p.encodings.length) p.encodings = [{}];
        p.encodings[0].maxBitrate = 48000;
        p.encodings[0].priority = 'high';
        p.encodings[0].networkPriority = 'high';
        await sender.setParameters(p);
      } catch (_) {}
    }
  }

  function formatDuration(totalSeconds) {
    const m = Math.floor(totalSeconds / 60), s = totalSeconds % 60;
    return String(m).padStart(2, '0') + ':' + String(s).padStart(2, '0');
  }

  class Tones {
    constructor() { this.ctx = null; this.current = null; }
    unlock() {
      const AC = global.AudioContext || global.webkitAudioContext;
      if (!AC) return;
      if (!this.ctx) this.ctx = new AC();
      if (this.ctx.state === 'suspended') this.ctx.resume().catch(() => {});
    }
    _bus(volume) {
      const ctx = this.ctx;
      const master = ctx.createGain();
      master.gain.value = volume;
      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass'; filter.frequency.value = 1800; filter.Q.value = 0.5;
      filter.connect(master); master.connect(ctx.destination);
      return { master, input: filter };
    }
    _note(bus, freq, at, dur, attack) {
      const ctx = this.ctx;
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = 'sine'; o.frequency.value = freq;
      g.gain.setValueAtTime(0, at);
      g.gain.linearRampToValueAtTime(1, at + attack);
      g.gain.setTargetAtTime(0, at + attack, dur / 4);
      o.connect(g); g.connect(bus.input);
      o.start(at); o.stop(at + dur + 0.1);
    }
    _loop(notes, cycle, volume, attack) {
      this.stop();
      this.unlock();
      if (!this.ctx) return;
      const bus = this._bus(volume);
      let next = this.ctx.currentTime + 0.05;
      const schedule = () => {
        while (next < this.ctx.currentTime + cycle + 0.5) {
          notes.forEach(([f, off, dur]) => this._note(bus, f, next + off, dur, attack));
          next += cycle;
        }
      };
      schedule();
      this.current = { bus, timer: setInterval(schedule, 250) };
    }
    ringback() { this._loop([[440, 0, 1.6]], 3.5, 0.07, 0.08); }
    ringtone() { this._loop([[659.25, 0, 0.7], [783.99, 0.22, 0.7], [987.77, 0.44, 1.0]], 2.6, 0.09, 0.01); }
    beep() {
      this.stop(); this.unlock();
      if (!this.ctx) return;
      const bus = this._bus(0.07), t = this.ctx.currentTime + 0.02;
      this._note(bus, 587.33, t, 0.35, 0.01);
      this._note(bus, 440, t + 0.18, 0.5, 0.01);
      setTimeout(() => bus.master.disconnect(), 1500);
    }
    stop() {
      const c = this.current;
      if (!c) return;
      this.current = null;
      clearInterval(c.timer);
      const now = this.ctx.currentTime;
      c.bus.master.gain.cancelScheduledValues(now);
      c.bus.master.gain.setValueAtTime(c.bus.master.gain.value, now);
      c.bus.master.gain.linearRampToValueAtTime(0, now + 0.08);
      setTimeout(() => c.bus.master.disconnect(), 200);
    }
  }

  class CallCenter {
    constructor(opts) {
      this.o = opts;
      this.side = opts.side;
      this.tones = new Tones();
      this.state = 'idle';
      this.busy = false;
      this.call = null;
      this.pc = null;
      this.dc = null;
      this.stream = null;
      this.muted = false;
      this.videoTx = null;
      this.screenTrack = null;
      this.remoteScreen = null;
      this.timers = {};
      this.incoming = null;
      this.dismissed = new Set();

      this.remoteAudio = new Audio();
      this.remoteAudio.autoplay = true;
      this.remoteAudio.setAttribute('playsinline', '');

      const unlock = () => { this.tones.unlock(); };
      document.addEventListener('pointerdown', unlock, { once: true });
      document.addEventListener('keydown', unlock, { once: true });

      global.addEventListener('pagehide', () => this._beaconEnd());
    }

    startIncomingPolling() {
      const tick = async () => {
        try {
          const { calls } = await api(this.o.urls.incoming + '?side=' + this.side);
          this._handleIncoming(calls);
        } catch (_) {}
        this.timers.incoming = setTimeout(tick, POLL_INCOMING_MS);
      };
      tick();
    }

    async dial(extra) {
      if (this.busy || !this._isFree()) return;
      this.busy = true;
      this.tones.unlock();
      this._setState('calling', { outgoing: true });
      try {
        await this._preparePeer();
        this.videoTx = this.pc.addTransceiver('video', { direction: 'sendrecv' });
        this.dc = this.pc.createDataChannel('control');
        this._wireDataChannel(this.dc);
        const offer = await this.pc.createOffer();
        await this.pc.setLocalDescription(offer);
        await waitForIce(this.pc);
        if (this.state !== 'calling') return;
        this.tones.ringback();
        const created = await api(this.o.urls.start, Object.assign(
          { side: this.side, offer: tuneOpus(this.pc.localDescription.sdp) }, extra || {}));
        if (this.state !== 'calling') {
          api(this.o.urls.end(created.id), { side: this.side }).catch(() => {});
          return;
        }
        this.call = created;
        this._pollCall();
      } catch (err) {
        this._fail(err);
      } finally {
        this.busy = false;
      }
    }

    async answer() {
      const incoming = this.incoming;
      if (this.busy || !incoming || !this._isFree()) return;
      this.busy = true;
      this.tones.stop();
      this.tones.unlock();
      this._clearIncoming();
      this._setState('connecting', { outgoing: false });
      try {
        const detail = await api(this.o.urls.detail(incoming.id) + '?side=' + this.side);
        if (detail.status !== 'ringing') throw new Error('The caller hung up.');
        this.call = detail;
        await this._preparePeer();
        await this.pc.setRemoteDescription({ type: 'offer', sdp: fixSdp(detail.offer) });
        this.videoTx = this.pc.getTransceivers().find((t) => t.receiver.track.kind === 'video') || null;
        if (this.videoTx) this.videoTx.direction = 'sendrecv';
        preferOpus(this.pc);
        const ans = await this.pc.createAnswer();
        await this.pc.setLocalDescription(ans);
        await waitForIce(this.pc);
        if (this.state !== 'connecting') return;
        this.call = await api(this.o.urls.answer(incoming.id), {
          side: this.side, answer: tuneOpus(this.pc.localDescription.sdp),
        });
        this._pollCall();
      } catch (err) {
        this._fail(err);
      } finally {
        this.busy = false;
      }
    }

    async decline() {
      const incoming = this.incoming;
      if (!incoming) return;
      this.dismissed.add(incoming.id);
      this._clearIncoming();
      this.tones.stop();
      try { await api(this.o.urls.end(incoming.id), { side: this.side }); } catch (_) {}
    }

    hangup() {
      if (this.state === 'idle' || this.state === 'ended') return;
      try { if (this.dc && this.dc.readyState === 'open') this.dc.send('bye'); } catch (_) {}
      this._sendEnd();
      this._finish('Call ended', true);
    }

    canShareScreen() {
      return !!(navigator.mediaDevices && navigator.mediaDevices.getDisplayMedia);
    }

    // Phones cannot capture their own screen, but they CAN share the camera.
    // This is the phone equivalent of screen share ("show me the problem").
    canShareCamera() {
      return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
    }

    async startCameraShare() {
      if (this.screenTrack || !this.videoTx || !this.pc || this.state === 'idle' || this.state === 'ended') return false;
      if (!this.canShareCamera()) {
        if (this.o.onError) this.o.onError('Camera sharing needs a secure (HTTPS) page.');
        return false;
      }
      let stream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: 'environment' },   // rear camera first
                   width: { ideal: 1280 }, height: { ideal: 720 },
                   frameRate: { ideal: SCREEN_FPS, max: SCREEN_FPS } },
          audio: false,
        });
      } catch (_) {
        if (this.o.onError) this.o.onError('Allow camera access to share (and make sure the site is on HTTPS).');
        return false;
      }
      if (!this.videoTx || !this.pc || this.state === 'idle' || this.state === 'ended') {
        stream.getTracks().forEach((t) => t.stop());
        return false;
      }
      const track = stream.getVideoTracks()[0];
      track.contentHint = 'motion';
      track.onended = () => this.stopScreenShare();
      this.screenTrack = track;
      await this.videoTx.sender.replaceTrack(track);
      try {
        const p = this.videoTx.sender.getParameters();
        if (!p.encodings || !p.encodings.length) p.encodings = [{}];
        p.encodings[0].maxBitrate = SCREEN_MAX_BITRATE;
        p.encodings[0].maxFramerate = SCREEN_FPS;
        await this.videoTx.sender.setParameters(p);
      } catch (_) {}
      this._send('screen-on');
      if (this.o.onLocalScreen) this.o.onLocalScreen(true);
      return true;
    }

    // Start screen share on desktop, camera on phones — whichever this device supports.
    startVideoShare() {
      return this.canShareScreen() ? this.startScreenShare() : this.startCameraShare();
    }

    async startScreenShare() {
      if (this.screenTrack || !this.videoTx || !this.pc || this.state === 'idle' || this.state === 'ended') return false;
      if (!this.canShareScreen()) {
        if (this.o.onError) this.o.onError('Screen sharing is not supported.');
        return false;
      }
      let stream;
      try {
        stream = await navigator.mediaDevices.getDisplayMedia({
          video: { frameRate: { ideal: SCREEN_FPS, max: SCREEN_FPS },
                   width: { max: 1920 }, height: { max: 1080 }, cursor: 'always' },
          audio: false,
        });
      } catch (_) { return false; }
      if (!this.videoTx || !this.pc || this.state === 'idle' || this.state === 'ended') {
        stream.getTracks().forEach((t) => t.stop());
        return false;
      }
      const track = stream.getVideoTracks()[0];
      track.contentHint = 'detail';
      track.onended = () => this.stopScreenShare();
      this.screenTrack = track;
      await this.videoTx.sender.replaceTrack(track);
      try {
        const p = this.videoTx.sender.getParameters();
        if (!p.encodings || !p.encodings.length) p.encodings = [{}];
        p.encodings[0].maxBitrate = SCREEN_MAX_BITRATE;
        p.encodings[0].maxFramerate = SCREEN_FPS;
        await this.videoTx.sender.setParameters(p);
      } catch (_) {}
      this._send('screen-on');
      if (this.o.onLocalScreen) this.o.onLocalScreen(true);
      return true;
    }

    async stopScreenShare() {
      const track = this.screenTrack;
      if (!track) return;
      this.screenTrack = null;
      track.onended = null;
      track.stop();
      if (this.videoTx) { try { await this.videoTx.sender.replaceTrack(null); } catch (_) {} }
      this._send('screen-off');
      if (this.o.onLocalScreen) this.o.onLocalScreen(false);
    }

    toggleMute() {
      if (!this.stream) return this.muted;
      this.muted = !this.muted;
      this.stream.getAudioTracks().forEach((t) => { t.enabled = !this.muted; });
      return this.muted;
    }

    _isFree() { return this.state === 'idle' || this.state === 'ended' || this.state === 'incoming'; }

    _setState(state, info) {
      this.state = state;
      if (this.o.onState) this.o.onState(state, Object.assign({ call: this.call }, info || {}));
    }

    async _preparePeer() {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error('Calls need HTTPS (or localhost).');
      }
      try {
        this.stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
          video: false,
        });
      } catch (e) {
        throw new Error('Microphone access is needed for calls.');
      }
      this.muted = false;

      const pc = new RTCPeerConnection({
        iceServers: this.o.iceServers,
        bundlePolicy: 'max-bundle',
        rtcpMuxPolicy: 'require',
        iceCandidatePoolSize: 2,
      });
      this.pc = pc;
      this.stream.getAudioTracks().forEach((t) => pc.addTrack(t, this.stream));
      preferOpus(pc);

      pc.ontrack = (e) => {
        if (e.track.kind === 'video') {
          try { e.receiver.jitterBufferTarget = 0; } catch (_) {}
          try { e.receiver.playoutDelayHint = 0; } catch (_) {}
          this.remoteScreen = new MediaStream([e.track]);
          return;
        }
        const stream = new MediaStream([e.track]);
        this.remoteAudio.srcObject = stream;
        this.remoteAudio.play().catch(() => {});
      };
      pc.ondatachannel = (e) => { this.dc = e.channel; this._wireDataChannel(e.channel); };
      pc.onconnectionstatechange = () => this._onConnectionState();
      pc.oniceconnectionstatechange = () => this._onConnectionState();
    }

    _wireDataChannel(dc) {
      dc.onmessage = (e) => {
        if (e.data === 'bye') this._finish('The other person ended the call', true);
        else if (e.data === 'screen-on' && this.o.onRemoteScreen) this.o.onRemoteScreen(this.remoteScreen);
        else if (e.data === 'screen-off' && this.o.onRemoteScreen) this.o.onRemoteScreen(null);
      };
    }

    _send(msg) {
      try { if (this.dc && this.dc.readyState === 'open') this.dc.send(msg); } catch (_) {}
    }

    _onConnectionState() {
      const pc = this.pc;
      if (!pc) return;
      const s = pc.connectionState || pc.iceConnectionState;
      if (s === 'connected' || s === 'completed') {
        clearTimeout(this.timers.reconnect);
        this.timers.reconnect = null;
        if (this.state !== 'active') {
          this.tones.stop();
          tuneSender(pc);
          this._setState('active');
          this._startTimer();
        }
      } else if (s === 'disconnected' && this.state === 'active') {
        this._setState('reconnecting');
        this.timers.reconnect = setTimeout(() => {
          this._sendEnd();
          this._finish('Call dropped – connection lost', true);
        }, RECONNECT_GRACE_MS);
      } else if (s === 'failed') {
        this._sendEnd();
        this._finish('Could not connect the call (network blocked). Try again.', true);
      }
    }

    _pollCall() {
      clearTimeout(this.timers.poll);
      const tick = async () => {
        if (!this.call || this.state === 'idle' || this.state === 'ended') return;
        try {
          const c = await api(this.o.urls.detail(this.call.id) + '?side=' + this.side);
          if (!this.call || c.id !== this.call.id) return;
          this.call = c;

          const isConnected = (c.status === 'connected' || c.status === 'active');

          if (isConnected && c.initiated_by === this.side &&
              this.pc && !this.pc.currentRemoteDescription && c.answer) {
            const answerSdp = fixSdp(c.answer);
            if (!answerSdp.startsWith('v=')) {
              console.error('[CallCenter] answer SDP is malformed. First 100 chars:', answerSdp.slice(0, 100));
              return this._finish('Call failed: malformed SDP', true);
            }

            this._setState('connecting', { outgoing: true });
            this.tones.stop();
            try {
              await this.pc.setRemoteDescription({ type: 'answer', sdp: answerSdp });
            } catch (err) {
              console.error('[CallCenter] setRemoteDescription failed:', err);
              return this._finish('Call failed: ' + err.message, true);
            }
          } else if (['ended', 'missed', 'declined'].includes(c.status)) {
            const reason = {
              declined: 'Call declined',
              missed: c.initiated_by === this.side ? 'No answer' : 'Call cancelled',
              ended: 'Call ended',
            }[c.status];
            return this._finish(reason, c.ended_by !== this.side);
          }
        } catch (err) {
          if (err.status === 404 || err.status === 403) return this._finish('Call ended', true);
        }
        this.timers.poll = setTimeout(tick, POLL_CALL_MS);
      };
      this.timers.poll = setTimeout(tick, POLL_CALL_MS);
    }

    _startTimer() {
      clearInterval(this.timers.clock);
      const started = Date.now();
      const draw = () => this.o.onTimer && this.o.onTimer(formatDuration(Math.floor((Date.now() - started) / 1000)));
      draw();
      this.timers.clock = setInterval(draw, 1000);
    }

    _sendEnd() {
      if (!this.call || !this.call.id) return;
      api(this.o.urls.end(this.call.id), { side: this.side }).catch(() => {});
    }

    _beaconEnd() {
      if (!this.call || !this.call.id || this.state === 'ended' || this.state === 'idle') return;
      try { if (this.dc && this.dc.readyState === 'open') this.dc.send('bye'); } catch (_) {}
      const body = new Blob([JSON.stringify({ side: this.side })], { type: 'application/json' });
      fetch(this.o.urls.end(this.call.id), {
        method: 'POST', keepalive: true, credentials: 'same-origin', body,
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      }).catch(() => {});
    }

    _fail(err) {
      this._sendEnd();
      this._finish(err && err.message ? err.message : 'Call failed', false);
      if (this.o.onError) this.o.onError(err && err.message ? err.message : 'Call failed');
    }

    _finish(reason, playBeep) {
      if (this.state === 'ended' || this.state === 'idle') return;
      clearTimeout(this.timers.poll);
      clearTimeout(this.timers.reconnect);
      clearInterval(this.timers.clock);
      this.timers.poll = this.timers.reconnect = this.timers.clock = null;
      this.tones.stop();

      if (this.dc) { this.dc.onmessage = null; try { this.dc.close(); } catch (_) {} }
      if (this.pc) {
        this.pc.ontrack = this.pc.ondatachannel = null;
        this.pc.onconnectionstatechange = this.pc.oniceconnectionstatechange = null;
        try { this.pc.close(); } catch (_) {}
      }
      if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
      if (this.screenTrack) {
        this.screenTrack.onended = null;
        this.screenTrack.stop();
        this.screenTrack = null;
        if (this.o.onLocalScreen) this.o.onLocalScreen(false);
      }
      if (this.o.onRemoteScreen) this.o.onRemoteScreen(null);
      this.remoteScreen = null;
      this.videoTx = null;
      this.remoteAudio.pause();
      this.remoteAudio.srcObject = null;

      if (this.call && this.call.id) this.dismissed.add(this.call.id);
      this.pc = this.dc = this.stream = null;
      const ended = this.call;
      this.call = null;
      this.muted = false;
      if (playBeep) this.tones.beep();
      this._setState('ended', { reason, call: ended });
    }

    _handleIncoming(calls) {
      const fresh = calls.filter((c) => !this.dismissed.has(c.id));
      if (this.incoming && !fresh.some((c) => c.id === this.incoming.id)) {
        this._clearIncoming();
        this.tones.stop();
      }
      if (!this.incoming && fresh.length && this._isFree() && !this.busy) {
        this.incoming = fresh[0];
        this.tones.ringtone();
        if (navigator.vibrate) navigator.vibrate([400, 200, 400]);
        if (this.o.onIncoming) this.o.onIncoming(this.incoming);
      }
    }

    _clearIncoming() {
      this.incoming = null;
      if (this.o.onIncoming) this.o.onIncoming(null);
    }
  }

  global.CallCenter = CallCenter;
})(window);