/* One iframe per listing. Removing it also stops its audio. */
(() => {
  let active = null;
  function close(button) {
    const region = document.getElementById(button.getAttribute('aria-controls'));
    region.replaceChildren();
    region.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    button.firstChild.textContent = 'Play set';
    active = null;
  }
  function enablePlayers() {
    document.querySelectorAll('[data-player-src][hidden]').forEach(button => {
      if (document.getElementById(button.getAttribute('aria-controls'))) button.hidden = false;
    });
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-player-src]');
    if (!button) return;
    const region = document.getElementById(button.getAttribute('aria-controls'));
    if (!region) return;
    if (active === button) { close(button); return; }
    if (active) close(active);
    const frame = document.createElement('iframe');
    frame.src = button.dataset.playerSrc;
    frame.title = button.dataset.playerTitle;
    frame.height = button.dataset.playerHeight;
    frame.width = '100%';
    frame.className = 'embedded-player';
    frame.allow = 'encrypted-media; fullscreen; autoplay';
    frame.allowFullscreen = true;
    region.append(frame);
    region.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    button.firstChild.textContent = 'Close player';
    active = button;
  });
  document.addEventListener('sets:added', enablePlayers);
  enablePlayers();
})();
