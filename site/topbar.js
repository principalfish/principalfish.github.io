const body = document.body;

if (body && !body.querySelector('.pf-topbar')) {

  const isHome = body.dataset.topbarHome === 'true';
  const backHref = body.dataset.topbarBackHref || '../';
  const email = body.dataset.topbarEmail || 'principalfish@gmail.com';
  const subject = encodeURIComponent(body.dataset.topbarSubject || 'Principal Fish enquiry');

  const topbar = document.createElement('div');
  topbar.className = 'pf-topbar';

  const inner = document.createElement('div');
  inner.className = 'pf-topbar-inner';

  if (isHome) {
    inner.classList.add('is-home');
  } else {
    const backLink = document.createElement('a');
    backLink.href = backHref;
    backLink.className = 'pf-topbar-back-link';

    const fish = document.createElement('img');
    fish.src = '/imgs/principal-fish-silly.svg';
    fish.alt = '';
    fish.className = 'pf-topbar-fish';
    fish.setAttribute('aria-hidden', 'true');

    backLink.appendChild(fish);
    backLink.append('← Back home');
    inner.appendChild(backLink);
  }

  const contact = document.createElement('a');
  contact.href = `mailto:${email}?subject=${subject}`;
  contact.className = 'pf-topbar-contact-link';
  contact.setAttribute('aria-label', 'Contact by email');
  contact.textContent = 'Contact';
  inner.appendChild(contact);

  topbar.appendChild(inner);

  const firstChild = body.firstElementChild;
  if (
    firstChild &&
    (firstChild.classList.contains('maps-background') || firstChild.classList.contains('bluey-background'))
  ) {
    firstChild.insertAdjacentElement('afterend', topbar);
  } else {
    body.prepend(topbar);
  }
}
