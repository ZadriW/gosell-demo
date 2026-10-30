/**
 * Ampliação da miniatura do produto (`.admin-stock__thumb`) nas tabelas
 * de estoque do painel admin e do vendedor.
 */
(() => {
    'use strict';

    const dialog = document.getElementById('admin-image-lightbox');
    if (!dialog) return;

    const imgEl = dialog.querySelector('.admin-image-lightbox__img');
    const titleEl = dialog.querySelector('.admin-image-lightbox__title');
    const closeBtns = dialog.querySelectorAll('[data-admin-image-lightbox-close]');
    const shell = document.querySelector('.admin-shell');

    if (!imgEl || !titleEl || !shell) return;

    function thumbIsVisible(thumb) {
        if (!thumb || thumb.tagName !== 'IMG') return false;
        const src = (thumb.getAttribute('src') || '').trim();
        if (!src) return false;
        if (getComputedStyle(thumb).visibility === 'hidden') return false;
        if (thumb.naturalWidth === 0 && thumb.complete) return false;
        return true;
    }

    function productLabelFromRow(thumb) {
        if (thumb.dataset.lightboxLabel) return thumb.dataset.lightboxLabel;
        const row = thumb.closest('.admin-stock__row, .admin-stockv__row');
        if (!row) return '';
        const name = row.querySelector('.admin-stock__product strong, .admin-stockv__name');
        return name ? name.textContent.trim() : '';
    }

    function markThumbs() {
        shell.querySelectorAll('.admin-stock__thumb').forEach(thumb => {
            const zoomable = thumbIsVisible(thumb);
            thumb.classList.toggle('admin-stock__thumb--no-zoom', !zoomable);
            if (zoomable) {
                const label = productLabelFromRow(thumb) || 'produto';
                thumb.setAttribute('title', 'Clique para ampliar a imagem');
                thumb.setAttribute('role', 'button');
                thumb.setAttribute('tabindex', '0');
                thumb.setAttribute('aria-label', `Ampliar imagem de ${label}`);
            } else {
                thumb.removeAttribute('title');
                thumb.removeAttribute('role');
                thumb.removeAttribute('tabindex');
                thumb.removeAttribute('aria-label');
            }
        });
    }

    function openLightbox(thumb) {
        const src = (thumb.getAttribute('src') || '').trim();
        if (!src) return;

        const label = productLabelFromRow(thumb);
        imgEl.src = src;
        imgEl.alt = label ? `Imagem ampliada: ${label}` : 'Imagem ampliada do produto';
        titleEl.textContent = label || 'Imagem do produto';

        if (typeof dialog.showModal === 'function') {
            dialog.showModal();
        }
        const closeBtn = dialog.querySelector('[data-admin-image-lightbox-close]');
        if (closeBtn && typeof closeBtn.focus === 'function') {
            closeBtn.focus();
        }
    }

    function closeLightbox() {
        if (dialog.open) dialog.close();
        imgEl.removeAttribute('src');
        imgEl.alt = '';
    }

    shell.addEventListener('click', event => {
        const thumb = event.target.closest('.admin-stock__thumb');
        if (!thumb || !thumbIsVisible(thumb)) return;
        event.preventDefault();
        event.stopPropagation();
        openLightbox(thumb);
    });

    shell.addEventListener('keydown', event => {
        const thumb = event.target.closest('.admin-stock__thumb');
        if (!thumb || !thumbIsVisible(thumb)) return;
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        openLightbox(thumb);
    });

    closeBtns.forEach(btn => {
        btn.addEventListener('click', () => closeLightbox());
    });

    dialog.addEventListener('click', event => {
        if (event.target === dialog) closeLightbox();
    });

    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        closeLightbox();
    });

    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && dialog.open) {
            event.preventDefault();
            closeLightbox();
        }
    });

    shell.querySelectorAll('.admin-stock__thumb').forEach(thumb => {
        thumb.addEventListener('error', () => {
            thumb.classList.add('admin-stock__thumb--no-zoom');
            thumb.removeAttribute('role');
            thumb.removeAttribute('tabindex');
        });
        if (thumb.complete) markThumbs();
        else thumb.addEventListener('load', markThumbs, { once: true });
    });

    markThumbs();

    const observer = new MutationObserver(() => markThumbs());
    observer.observe(shell, { childList: true, subtree: true });
})();
