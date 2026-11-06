(() => {
  if ('serviceWorker' in navigator && window.isSecureContext) {
    navigator.serviceWorker.register('/sw.js')
      .catch(error => console.error('Mise en cache hors ligne indisponible.', error));
  }

  const button = document.getElementById('enable-notifications');
  if (!button) return;

  let audioContext;
  const beep = async () => {
    try {
      const AudioContext = window.AudioContext || window.webkitAudioContext;
      if (!AudioContext) return;
      audioContext ||= new AudioContext();
      if (audioContext.state === 'suspended') await audioContext.resume();
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.frequency.value = 1100;
      gain.gain.value = 0.12;
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      oscillator.start();
      oscillator.stop(audioContext.currentTime + 0.09);
    } catch (error) {
      console.warn('Le bip sonore est indisponible.', error);
    }
  };
  window.scannerBeep = beep;

  button.addEventListener('click', async () => {
    await beep();
    if (!window.isSecureContext || !('serviceWorker' in navigator)
        || !('PushManager' in window) || !('Notification' in window)) {
      button.textContent = 'HTTPS sécurisé requis';
      return;
    }

    button.disabled = true;
    try {
      const permission = Notification.permission === 'granted'
        ? 'granted' : await Notification.requestPermission();
      if (permission !== 'granted') throw new Error('Autorisez les notifications dans le navigateur.');

      const configResponse = await fetch('/api/push-config');
      const config = await configResponse.json();
      if (!configResponse.ok || !config.enabled) {
        throw new Error(config.reason || 'Web Push indisponible sur ce serveur.');
      }

      const registration = await navigator.serviceWorker.register('/sw.js');
      await navigator.serviceWorker.ready;
      let subscription = await registration.pushManager.getSubscription();
      if (!subscription) {
        const key = Uint8Array.from(
          atob(config.public_key.replace(/-/g, '+').replace(/_/g, '/')
            .padEnd(Math.ceil(config.public_key.length / 4) * 4, '=')),
          character => character.charCodeAt(0));
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: key
        });
      }

      const response = await fetch('/api/push-subscriptions', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({subscription: subscription.toJSON()})
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.error || 'Impossible d’enregistrer cet appareil.');
      }
      button.textContent = 'Alertes activées';
    } catch (error) {
      button.textContent = 'Activer les alertes';
      button.title = error.message;
      alert(error.message);
    } finally {
      button.disabled = false;
    }
  });
})();
