// Mert Ders Takip — ortak arayüz davranışları
(function () {
    const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';

    // CSRF başlığıyla JSON POST
    window.postJSON = function (url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
            body: JSON.stringify(body)
        }).then(async (res) => {
            const data = await res.json().catch(() => ({}));
            if (!res.ok || data.success === false) {
                throw new Error(data.error || 'İşlem tamamlanamadı.');
            }
            return data;
        });
    };

    // Modallar: data-open="id" açar, data-close kapatır, arka plana tıklayınca kapanır
    window.openModal = function (id) {
        const dlg = document.getElementById(id);
        if (dlg && !dlg.open) dlg.showModal();
        return dlg;
    };

    document.addEventListener('click', function (e) {
        const opener = e.target.closest('[data-open]');
        if (opener) {
            openModal(opener.dataset.open);
            return;
        }
        const closer = e.target.closest('[data-close]');
        if (closer) {
            closer.closest('dialog')?.close();
            return;
        }
        if (e.target.tagName === 'DIALOG' && e.target.open) {
            const r = e.target.getBoundingClientRect();
            const inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
            if (!inside) e.target.close();
        }
    });

    // Onay isteyen formlar: data-confirm="mesaj"
    document.addEventListener('submit', function (e) {
        const msg = e.target.dataset.confirm;
        if (msg && !window.confirm(msg)) e.preventDefault();
    });

    // Tema: açık (varsayılan) / koyu — seçim tarayıcıda saklanır
    const root = document.documentElement;
    const themeMeta = document.querySelector('meta[name="theme-color"]');

    function syncThemeUi() {
        const dark = root.dataset.theme === 'dark';
        if (themeMeta) themeMeta.content = dark ? '#0E0D0B' : '#F4F1EA';
        document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
            btn.setAttribute('aria-label', dark ? 'Açık temaya geç' : 'Koyu temaya geç');
        });
    }

    document.querySelectorAll('[data-theme-toggle]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            const next = root.dataset.theme === 'dark' ? 'light' : 'dark';
            root.classList.add('theme-switching');
            if (next === 'dark') root.dataset.theme = 'dark';
            else delete root.dataset.theme;
            try { localStorage.setItem('theme', next); } catch (e) {}
            syncThemeUi();
            document.dispatchEvent(new CustomEvent('themechange', { detail: next }));
            setTimeout(() => root.classList.remove('theme-switching'), 250);
        });
    });
    syncThemeUi();

    // Mobil menü
    const menuBtn = document.querySelector('.menu-btn');
    const nav = document.getElementById('main-nav');
    if (menuBtn && nav) {
        menuBtn.addEventListener('click', function () {
            const open = nav.classList.toggle('open');
            menuBtn.setAttribute('aria-expanded', String(open));
        });
    }

    // Bildirimler 5 sn sonra kaybolur, × ile hemen kapanır
    function dismiss(el) {
        el.classList.add('leaving');
        setTimeout(() => el.remove(), 220);
    }
    document.querySelectorAll('.flash').forEach(function (el) {
        el.querySelector('button')?.addEventListener('click', () => dismiss(el));
        if (!el.classList.contains('danger')) setTimeout(() => dismiss(el), 5000);
    });

    // Sayfa içinden bildirim göstermek için
    window.toast = function (message, kind) {
        let box = document.querySelector('.flashes');
        if (!box) {
            box = document.createElement('div');
            box.className = 'flashes';
            box.setAttribute('role', 'status');
            document.body.appendChild(box);
        }
        const el = document.createElement('div');
        el.className = 'flash ' + (kind || 'info');
        const p = document.createElement('p');
        p.textContent = message;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'icon-btn';
        btn.setAttribute('aria-label', 'Kapat');
        btn.innerHTML = '<i class="bi bi-x-lg" aria-hidden="true"></i>';
        btn.addEventListener('click', () => dismiss(el));
        el.append(p, btn);
        box.appendChild(el);
        if (kind !== 'danger') setTimeout(() => dismiss(el), 5000);
    };
})();
