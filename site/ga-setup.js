// Google Analytics runtime gating for dev/localhost
(function () {
  const hostname = window.location.hostname;
  const isDevelopmentHost =
    window.location.protocol === 'file:' ||
    hostname === 'localhost' ||
    hostname === '127.0.0.1' ||
    hostname === '0.0.0.0' ||
    hostname === '[::1]' ||
    hostname.endsWith('.local');

  if (isDevelopmentHost) return;

  const script = document.createElement('script');
  script.async = true;
  script.src = 'https://www.googletagmanager.com/gtag/js?id=G-DF15MKHP0V';
  document.head.appendChild(script);

  window.dataLayer = window.dataLayer || [];
  window.gtag = function () { window.dataLayer.push(arguments); };
  window.gtag('js', new Date());
  window.gtag('config', 'G-DF15MKHP0V', { cookie_domain: 'auto' });
})();
