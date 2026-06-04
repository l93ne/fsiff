/* ── Header scroll effect ── */
const header = document.getElementById('header');
window.addEventListener('scroll', () => {
    header.classList.toggle('scrolled', window.scrollY > 10);
}, { passive: true });

/* ── Mobile nav ── */
const burger = document.getElementById('burger');
const nav    = document.getElementById('nav');

burger.addEventListener('click', () => {
    const open = nav.classList.toggle('open');
    burger.classList.toggle('open', open);
    burger.setAttribute('aria-expanded', String(open));
});

nav.querySelectorAll('.nav__link').forEach(link => {
    link.addEventListener('click', () => {
        nav.classList.remove('open');
        burger.classList.remove('open');
        burger.setAttribute('aria-expanded', 'false');
    });
});

/* ── Active nav link on scroll ── */
const sections = document.querySelectorAll('section[id]');
const navLinks  = document.querySelectorAll('.nav__link');

const secObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            navLinks.forEach(l => l.classList.remove('active'));
            const active = document.querySelector(`.nav__link[href="#${entry.target.id}"]`);
            if (active) active.classList.add('active');
        }
    });
}, { rootMargin: '-40% 0px -55% 0px' });

sections.forEach(s => secObserver.observe(s));

/* ── Scroll-reveal ── */
const revealObserver = new IntersectionObserver(entries => {
    entries.forEach(entry => {
        if (entry.isIntersecting) {
            entry.target.classList.add('visible');
            revealObserver.unobserve(entry.target);
        }
    });
}, { threshold: 0.08, rootMargin: '0px 0px -40px 0px' });

document.querySelectorAll('.fade-up').forEach(el => revealObserver.observe(el));

/* ── FAQ accordion ── */
document.querySelectorAll('.faq__q').forEach(btn => {
    btn.addEventListener('click', () => {
        const item = btn.closest('.faq__item');
        const isOpen = item.classList.contains('open');

        document.querySelectorAll('.faq__item.open').forEach(i => i.classList.remove('open'));
        if (!isOpen) item.classList.add('open');
    });
});

/* ── Pricing period toggle ── */
const ptoggle = document.getElementById('ptoggle');
const pcards  = document.querySelectorAll('.pcard');

if (ptoggle) {
    ptoggle.querySelectorAll('.ptoggle__btn').forEach(btn => {
        btn.addEventListener('click', () => {
            ptoggle.querySelectorAll('.ptoggle__btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            const p = btn.dataset.period;

            pcards.forEach(card => {
                const amount  = card.querySelector('.pcard__amount');
                const period  = card.querySelector('.pcard__period');
                const saving  = card.querySelector('.pcard__saving');
                const buyBtn  = card.querySelector('.js-buy');

                if (amount)  amount.textContent  = amount.dataset[`p${p}`];
                if (period)  period.textContent  = period.dataset[`l${p}`];
                if (saving)  saving.hidden = (p === '1');
                if (buyBtn)  buyBtn.dataset.planId = card.dataset[`id${p}`];
            });
        });
    });
}

/* ── Footer year ── */
const yearEl = document.getElementById('year');
if (yearEl) yearEl.textContent = new Date().getFullYear();
