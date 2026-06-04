document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('contactForm');
    if (!form) return;

    form.addEventListener('submit', (e) => {
        e.preventDefault();
        const data = Object.fromEntries(new FormData(form));
        console.log('Form submitted:', data);
        // Здесь подключите вашу логику отправки (fetch, EmailJS и т.д.)
        alert('Сообщение отправлено!');
        form.reset();
    });
});
