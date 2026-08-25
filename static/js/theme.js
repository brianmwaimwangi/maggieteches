// Keeps the moon/sun icon in sync with the current theme and lets the
// navbar button flip data-bs-theme, persisting the choice for next visit.
// The actual "apply theme before first paint" step lives inline in <head>
// of each page (see the small snippet included via _theme_head_script.html)
// so there's no flash of the wrong theme while this file loads.
function toggleTheme() {
  var html = document.documentElement;
  var current = html.getAttribute('data-bs-theme') || 'light';
  var next = current === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-bs-theme', next);
  try { localStorage.setItem('theme', next); } catch (e) {}
  updateThemeIcon(next);
}

function updateThemeIcon(theme) {
  document.querySelectorAll('.theme-toggle-icon').forEach(function (el) {
    el.className = 'theme-toggle-icon bi ' + (theme === 'dark' ? 'bi-sun' : 'bi-moon-stars');
  });
}

document.addEventListener('DOMContentLoaded', function () {
  var current = document.documentElement.getAttribute('data-bs-theme') || 'light';
  updateThemeIcon(current);
});
