async function loadComponent(placeholderId, filePath) {
    const placeholder = document.getElementById(placeholderId);
    if (!placeholder) return;

    try {
        const res = await fetch(filePath);
        if (!res.ok) throw new Error(`Failed to load ${filePath}`);
        const html = await res.text();
        placeholder.outerHTML = html;
    } catch (err) {
        console.error(err);
    }
}

function resolveComponentPath(filename) {
    const depth = (window.location.pathname.match(/\//g) || []).length - 1;
    const prefix = depth > 0 ? '../'.repeat(depth) : '';
    return `${prefix}components/${filename}`;
}

document.addEventListener('DOMContentLoaded', () => {
    loadComponent('header-placeholder', resolveComponentPath('header.html')).then(() => {
        highlightActiveLink();
        initMobileMenu();
    });
    loadComponent('footer-placeholder', resolveComponentPath('footer.html')).then(() => {
        const yearEl = document.getElementById('year');
        if (yearEl) yearEl.textContent = new Date().getFullYear();
    });
});

function highlightActiveLink() {
    const path = window.location.pathname;
    document.querySelectorAll('.nav__link').forEach(link => {
        const href = link.getAttribute('href');
        const isHome = (href === '/' || href === '/index.html') && (path === '/' || path.endsWith('/index.html'));
        const isMatch = !isHome && href && path.endsWith(href.replace(/^\//, ''));
        if (isHome || isMatch) link.classList.add('active');
    });
}

function initMobileMenu() {
    const toggle = document.querySelector('.nav-toggle');
    const nav = document.querySelector('.nav');
    if (!toggle || !nav) return;

    toggle.addEventListener('click', () => {
        const open = nav.classList.toggle('open');
        toggle.setAttribute('aria-expanded', String(open));
    });
}
