const PLAN_LABELS = {
    indiv_1: { name: 'Индивидуальный', price: '99 ₽',  period: '1 месяц',   devices: 'до 3 устройств' },
    indiv_3: { name: 'Индивидуальный', price: '249 ₽', period: '3 месяца',  devices: 'до 3 устройств' },
    pair_1:  { name: 'Парный',         price: '169 ₽', period: '1 месяц',   devices: 'до 6 устройств' },
    pair_3:  { name: 'Парный',         price: '429 ₽', period: '3 месяца',  devices: 'до 6 устройств' },
};

const modal        = document.getElementById('modal');
const modalOverlay = document.getElementById('modal-overlay');
const modalClose   = document.getElementById('modal-close');
const modalTitle   = document.getElementById('modal-title');
const modalDesc    = document.getElementById('modal-desc');
const modalPriceLine = document.getElementById('modal-price-line');
const modalForm    = document.getElementById('modal-form');
const emailInput   = document.getElementById('m-email');
const emailErr     = document.getElementById('m-email-err');
const payBtn       = document.getElementById('modal-pay-btn');
const errMsg       = document.getElementById('modal-err-msg');

let currentPlanId = null;

/* ── Open modal ── */
document.querySelectorAll('.js-buy').forEach(btn => {
    btn.addEventListener('click', () => {
        const planId = btn.dataset.planId;
        const plan   = PLAN_LABELS[planId];
        if (!plan) return;

        currentPlanId = planId;

        modalTitle.textContent = 'Оформление подписки';
        modalDesc.textContent  = `${plan.name} · ${plan.devices}`;
        modalPriceLine.innerHTML =
            `Тариф: <strong>${plan.name}</strong> · ${plan.period} · <strong>${plan.price}</strong>`;

        emailInput.value = '';
        emailErr.textContent = '';
        errMsg.textContent   = '';
        errMsg.classList.add('hidden');
        setLoading(false);

        modal.classList.add('open');
        document.body.style.overflow = 'hidden';
        setTimeout(() => emailInput.focus(), 250);
    });
});

/* ── Close modal ── */
function closeModal() {
    modal.classList.remove('open');
    document.body.style.overflow = '';
}

modalClose.addEventListener('click', closeModal);
modalOverlay.addEventListener('click', closeModal);
document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && modal.classList.contains('open')) closeModal();
});

/* ── Form submit ── */
modalForm.addEventListener('submit', async e => {
    e.preventDefault();

    const email = emailInput.value.trim();
    if (!validateEmail(email)) {
        emailInput.classList.add('error');
        emailErr.textContent = 'Введите корректный email';
        emailInput.focus();
        return;
    }

    emailInput.classList.remove('error');
    emailErr.textContent = '';
    errMsg.classList.add('hidden');
    setLoading(true);

    try {
        const res = await fetch('/api/web/create-payment', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plan_id: currentPlanId, email }),
        });

        if (!res.ok) {
            const data = await res.json().catch(() => ({}));
            throw new Error(data.detail || data.message || `Ошибка сервера (${res.status})`);
        }

        const { payment_url } = await res.json();
        if (!payment_url) throw new Error('Не получена ссылка на оплату');

        window.location.href = payment_url;

    } catch (err) {
        setLoading(false);
        errMsg.textContent = err.message || 'Что-то пошло не так. Попробуйте ещё раз.';
        errMsg.classList.remove('hidden');
    }
});

emailInput.addEventListener('input', () => {
    emailInput.classList.remove('error');
    emailErr.textContent = '';
});

/* ── Helpers ── */
function setLoading(on) {
    payBtn.disabled = on;
    payBtn.querySelector('.btn-text').classList.toggle('hidden', on);
    payBtn.querySelector('.btn-spin').classList.toggle('hidden', !on);
}

function validateEmail(v) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v);
}
