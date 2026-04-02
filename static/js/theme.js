(function() {
    const STORAGE_KEY = 'truani-theme';

    function getPreferred() {
        const stored = localStorage.getItem(STORAGE_KEY);
        if (stored) return stored;
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    function applyIcons(theme) {
        document.querySelectorAll('.theme-icon-sun').forEach(el => el.style.display = theme === 'dark' ? '' : 'none');
        document.querySelectorAll('.theme-icon-moon').forEach(el => el.style.display = theme === 'light' ? '' : 'none');
    }

    function apply(theme) {
        document.documentElement.classList.add('no-transitions');
        document.documentElement.setAttribute('data-theme', theme);
        localStorage.setItem(STORAGE_KEY, theme);
        applyIcons(theme);
        requestAnimationFrame(() => {
            requestAnimationFrame(() => document.documentElement.classList.remove('no-transitions'));
        });
    }

    // Set data-theme immediately (before paint) for colors
    var theme = getPreferred();
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem(STORAGE_KEY, theme);

    // Icons need the DOM — apply once ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => applyIcons(theme));
    } else {
        applyIcons(theme);
    }

    // Expose toggle
    window.toggleTheme = function() {
        var current = document.documentElement.getAttribute('data-theme') || 'light';
        apply(current === 'dark' ? 'light' : 'dark');
    };

    // Listen for OS theme changes
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
        if (!localStorage.getItem(STORAGE_KEY)) {
            apply(e.matches ? 'dark' : 'light');
        }
    });
})();
