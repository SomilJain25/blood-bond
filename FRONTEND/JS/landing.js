'use strict';

(function initReveal() {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const els = document.querySelectorAll('[data-reveal]');
  if (!els.length || reduceMotion) {
    els.forEach((el) => el.classList.add('revealed'));
    return;
  }
  const obs = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('revealed');
          obs.unobserve(entry.target);
        }
      });
    },
    { root: null, rootMargin: '0px 0px -8% 0px', threshold: 0.08 }
  );
  els.forEach((el) => obs.observe(el));
})();

function initStatCount() {
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const nodes = document.querySelectorAll('.hstat-val[data-count]');
  if (!nodes.length || reduceMotion) {
    nodes.forEach((el) => {
      const t = el.getAttribute('data-count');
      if (t) el.textContent = Number(t).toLocaleString();
    });
    return;
  }
  const animate = (el, target, duration) => {
    const start = performance.now();
    const from = 0;
    const tick = (now) => {
      const p = Math.min(1, (now - start) / duration);
      const eased = 1 - (1 - p) ** 3;
      const val = Math.round(from + (target - from) * eased);
      el.textContent = val.toLocaleString();
      if (p < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  const obs = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const el = entry.target;
        const raw = el.getAttribute('data-count');
        const target = raw ? parseInt(raw, 10) : 0;
        if (!Number.isFinite(target)) return;
        const duration = target > 10000 ? 1600 : target > 1000 ? 1400 : 900;
        animate(el, target, duration);
        obs.unobserve(el);
      });
    },
    { threshold: 0.25 }
  );
  nodes.forEach((el) => obs.observe(el));
}

// Real numbers from the API (aggregate counts only). If the API is unreachable the counters
// simply stay at 0 / "—" instead of showing made-up figures.
async function loadPublicStats() {
  try {
    const st = await Auth.apiFetch('/public/stats', { withAuth: false, timeoutMs: 8000 });
    document.getElementById('statDonors').setAttribute('data-count', String(st.donors_total || 0));
    document.getElementById('statHospitals').setAttribute('data-count', String(st.hospitals_total || 0));
    const match = document.getElementById('statMatch');
    const unit = document.getElementById('statMatchUnit');
    if (st.avg_match_seconds == null) {
      match.removeAttribute('data-count');
      match.textContent = '—';
      unit.textContent = '';
    } else if (st.avg_match_seconds < 1) {
      match.removeAttribute('data-count');
      match.textContent = '<1';
      unit.textContent = 'Second';
    } else if (st.avg_match_seconds < 120) {
      match.setAttribute('data-count', String(st.avg_match_seconds));
      unit.textContent = 'Seconds';
    } else {
      match.setAttribute('data-count', String(Math.round(st.avg_match_seconds / 60)));
      unit.textContent = 'Minutes';
    }
  } catch (_) {
    /* leave the zero state */
  }
}
loadPublicStats().finally(initStatCount);

(function initNavAndCursor() {
  const nav = document.getElementById('nav');
  const hamburger = document.getElementById('hamburger');
  const mobileMenu = document.getElementById('mobileMenu');
  const cursor = document.getElementById('cursor');
  const follower = document.getElementById('cursorFollower');

  function closeMobile() {
    if (mobileMenu) mobileMenu.classList.remove('open');
    if (hamburger) {
      hamburger.classList.remove('open');
      hamburger.setAttribute('aria-expanded', 'false');
    }
    document.body.style.overflow = '';
  }

  window.closeMobile = closeMobile;

  if (hamburger && mobileMenu) {
    hamburger.addEventListener('click', () => {
      const open = mobileMenu.classList.toggle('open');
      hamburger.classList.toggle('open', open);
      hamburger.setAttribute('aria-expanded', open ? 'true' : 'false');
      document.body.style.overflow = open ? 'hidden' : '';
    });
  }

  if (nav) {
    const onScroll = () => {
      nav.classList.toggle('scrolled', window.scrollY > 24);
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });
  }

  const finePointer = window.matchMedia('(pointer: fine)').matches;
  if (!finePointer || !cursor || !follower) return;

  let mx = 0;
  let my = 0;
  let fx = 0;
  let fy = 0;
  let raf = 0;
  let inited = false;

  function tickFollower() {
    raf = 0;
    fx += (mx - fx) * 0.22;
    fy += (my - fy) * 0.22;
    follower.style.left = `${fx}px`;
    follower.style.top = `${fy}px`;
    if (Math.abs(mx - fx) > 0.5 || Math.abs(my - fy) > 0.5) {
      raf = requestAnimationFrame(tickFollower);
    }
  }

  document.addEventListener(
    'mousemove',
    (e) => {
      mx = e.clientX;
      my = e.clientY;
      if (!inited) {
        fx = mx;
        fy = my;
        follower.style.left = `${fx}px`;
        follower.style.top = `${fy}px`;
        inited = true;
      }
      cursor.style.left = `${mx}px`;
      cursor.style.top = `${my}px`;
      if (!raf) raf = requestAnimationFrame(tickFollower);
    },
    { passive: true }
  );

  document.querySelectorAll('a, button, .btn-nav, .btn-hero-primary, .role-card').forEach((el) => {
    el.addEventListener('mouseenter', () => document.body.classList.add('cursor-hover'));
    el.addEventListener('mouseleave', () => document.body.classList.remove('cursor-hover'));
  });
})();

(function initPointCards() {
  const cards = document.querySelectorAll('.point');
  if (!cards.length) return;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (reduceMotion) return;
  cards.forEach((card, idx) => {
    card.style.opacity = '0';
    card.style.transform = 'translateY(10px)';
    setTimeout(() => {
      card.style.transition = 'opacity 0.35s ease, transform 0.35s ease';
      card.style.opacity = '1';
      card.style.transform = 'translateY(0)';
    }, 120 + idx * 90);
  });
})();
