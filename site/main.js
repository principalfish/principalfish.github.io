const cards = document.querySelectorAll('.app-card');

cards.forEach((card, index) => {
  card.style.opacity = '0';
  card.style.transform = 'translateY(12px)';

  requestAnimationFrame(() => {
    setTimeout(() => {
      card.style.transition = 'opacity 240ms ease, transform 240ms ease';
      card.style.opacity = '1';
      card.style.transform = 'translateY(0)';
    }, index * 90);
  });
});
