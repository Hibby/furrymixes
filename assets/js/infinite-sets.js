/* Progressive enhancement: fetch Hugo's next static page, never third-party data. */
(() => {
  const list = document.querySelector('[data-infinite-sets]');
  const pagination = document.querySelector('.pagination');
  const nextLink = pagination?.querySelector('[rel="next"]');
  if (!list || !nextLink) return;

  let nextURL = nextLink.href;
  let loading = false;
  const controls = document.createElement('div');
  controls.className = 'load-more';
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'button';
  button.textContent = 'Load more sets';
  const status = document.createElement('p');
  status.setAttribute('role', 'status');
  controls.append(button, status);
  list.after(controls);

  const observer = 'IntersectionObserver' in window
    ? new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) loadMore();
    }, { rootMargin: '400px' })
    : null;

  async function loadMore() {
    if (loading || !nextURL) return;
    loading = true;
    observer?.disconnect();
    button.setAttribute('aria-disabled', 'true');
    list.setAttribute('aria-busy', 'true');
    status.textContent = 'Loading more sets…';
    try {
      const response = await fetch(nextURL);
      if (!response.ok) throw new Error('Page unavailable');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const cards = page.querySelectorAll('[data-infinite-sets] > .set-card');
      if (!cards.length) throw new Error('No recording cards found');
      const next = page.querySelector('.pagination [rel="next"]');
      const followingURL = next ? new URL(next.getAttribute('href'), response.url).href : '';
      if (followingURL && new URL(followingURL).origin !== location.origin) {
        throw new Error('Unexpected pagination URL');
      }
      const fragment = document.createDocumentFragment();
      cards.forEach(card => fragment.append(card));
      list.append(fragment);
      document.dispatchEvent(new Event('sets:added'));
      nextURL = followingURL;
      button.textContent = nextURL ? 'Load more sets' : 'All sets loaded';
      button.setAttribute('aria-disabled', String(!nextURL));
      status.textContent = nextURL
        ? `${list.children.length} sets shown.`
        : 'You’ve reached the end of the archive.';
      pagination.hidden = true;
      if (nextURL) observer?.observe(button);
    } catch {
      button.setAttribute('aria-disabled', 'false');
      button.textContent = 'Try loading again';
      status.textContent = 'Couldn’t load more sets. Try again or continue using the page links.';
      nextLink.href = nextURL;
      const label = pagination.querySelector('span');
      if (label) label.textContent = 'Continue browsing by page';
      pagination.hidden = false;
    } finally {
      loading = false;
      list.setAttribute('aria-busy', 'false');
    }
  }

  button.addEventListener('click', loadMore);
  pagination.hidden = true;
  observer?.observe(button);
})();
