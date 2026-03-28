// Sidebar conversation management — shared across all pages
(function () {
  const STORAGE_KEY = 'openail_sessions';
  const CURRENT_KEY = 'openail_current_session';

  // One-time cleanup: remove legacy example sessions
  if (!localStorage.getItem('openail_v4')) {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(CURRENT_KEY);
    localStorage.removeItem('openail_examples_v2');
    localStorage.removeItem('openail_v3_clean');
    localStorage.setItem('openail_v4', '1');
  }

  function getSessions() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY)) || [];
    } catch {
      return [];
    }
  }

  function saveSessions(sessions) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions));
  }

  function getCurrentId() {
    return localStorage.getItem(CURRENT_KEY);
  }

  function setCurrentId(id) {
    localStorage.setItem(CURRENT_KEY, id);
  }

  // Initialize: start with empty sidebar (like ChatGPT)
  function init() {
    const sessions = getSessions();
    const currentId = getCurrentId();
    renderSidebar(sessions, currentId);
  }

  function renderSidebar(sessions, currentId) {
    const container = document.getElementById('session-list');
    if (!container) return;

    container.innerHTML = '';

    // Close any open menu when clicking elsewhere
    document.addEventListener('click', function () {
      document.querySelectorAll('.session-menu').forEach(function (m) {
        m.classList.add('hidden');
      });
    });

    sessions.forEach(function (session) {
      const isActive = session.id === currentId;

      // Wrapper div for hover group
      const wrapper = document.createElement('div');
      wrapper.className = 'relative group';

      // Session button
      const item = document.createElement('button');
      item.className =
        'w-full flex items-center gap-2 px-3 py-2.5 rounded-xl text-left text-sm transition-all ' +
        (isActive
          ? 'bg-primary-fixed/40 text-primary font-semibold'
          : 'text-on-surface-variant hover:bg-surface-container-high');

      item.innerHTML =
        '<span class="material-symbols-outlined text-base" style="font-variation-settings: \'FILL\' ' +
        (isActive ? '1' : '0') +
        ';">chat_bubble</span>' +
        '<span class="truncate flex-grow">' +
        escapeHtml(session.name) +
        '</span>';

      item.addEventListener('click', function () {
        if (!isActive) {
          setCurrentId(session.id);
          window.location.href = '/?session_id=' + session.id;
        }
      });

      // Three-dot menu button
      const menuBtn = document.createElement('button');
      menuBtn.className =
        'absolute right-2 top-1/2 -translate-y-1/2 p-1 rounded-md opacity-0 group-hover:opacity-100 hover:bg-slate-200 transition-all z-10';
      menuBtn.innerHTML = '<span class="material-symbols-outlined text-sm text-slate-400">more_horiz</span>';
      menuBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        // Close all other menus
        document.querySelectorAll('.session-menu').forEach(function (m) {
          m.classList.add('hidden');
        });
        // Toggle this menu
        menu.classList.toggle('hidden');
      });

      // Dropdown menu
      const menu = document.createElement('div');
      menu.className =
        'session-menu hidden absolute right-0 top-full mt-1 bg-white border border-slate-200 rounded-xl shadow-lg py-1 z-20 min-w-[120px]';

      const deleteBtn = document.createElement('button');
      deleteBtn.className =
        'w-full flex items-center gap-2 px-3 py-2 text-sm text-error hover:bg-error/5 transition-colors text-left';
      deleteBtn.innerHTML =
        '<span class="material-symbols-outlined text-base">delete</span>' +
        '<span>Delete</span>';
      deleteBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        menu.classList.add('hidden');
        showDeleteConfirm(session.id, session.name);
      });

      menu.appendChild(deleteBtn);
      wrapper.appendChild(item);
      wrapper.appendChild(menuBtn);
      wrapper.appendChild(menu);
      container.appendChild(wrapper);
    });
  }

  function showDeleteConfirm(sessionId, sessionName) {
    // Create modal overlay
    const overlay = document.createElement('div');
    overlay.className = 'fixed inset-0 bg-black/30 flex items-center justify-center z-[100]';
    overlay.id = 'deleteModal';

    overlay.innerHTML =
      '<div class="bg-white rounded-3xl p-8 max-w-sm mx-4 shadow-xl">' +
      '  <div class="flex items-center gap-3 mb-4">' +
      '    <span class="material-symbols-outlined text-error text-2xl">warning</span>' +
      '    <h3 class="font-headline font-bold text-lg text-on-surface">Delete Conversation</h3>' +
      '  </div>' +
      '  <p class="font-body text-sm text-on-surface-variant mb-6">Are you sure you want to delete "<strong>' + escapeHtml(sessionName) + '</strong>"? This will remove the conversation, diagnosis, and lesson data. This action cannot be undone.</p>' +
      '  <div class="flex gap-3 justify-end">' +
      '    <button id="cancelDelete" class="px-5 py-2.5 rounded-xl text-sm font-headline font-bold text-on-surface-variant hover:bg-slate-100 transition-colors">Cancel</button>' +
      '    <button id="confirmDelete" class="px-5 py-2.5 rounded-xl text-sm font-headline font-bold text-white bg-error hover:bg-error/90 transition-colors shadow-sm">Delete</button>' +
      '  </div>' +
      '</div>';

    document.body.appendChild(overlay);

    // Close on overlay click
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) {
        overlay.remove();
      }
    });

    document.getElementById('cancelDelete').addEventListener('click', function () {
      overlay.remove();
    });

    document.getElementById('confirmDelete').addEventListener('click', function () {
      deleteSession(sessionId);
      overlay.remove();
    });
  }

  function deleteSession(sessionId) {
    var sessions = getSessions();
    var currentId = getCurrentId();

    sessions = sessions.filter(function (s) { return s.id !== sessionId; });
    saveSessions(sessions);

    // If we deleted the current session, switch to another
    if (sessionId === currentId) {
      if (sessions.length > 0) {
        setCurrentId(sessions[0].id);
        window.location.href = '/';
      } else {
        // No sessions left, create a new one
        localStorage.removeItem(CURRENT_KEY);
        window.location.href = '/';
      }
    } else {
      renderSidebar(sessions, getCurrentId());
    }
  }

  function escapeHtml(text) {
    var div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
  }

  // New session handler — just go to clean index page
  window.createNewSession = function () {
    window.location.href = '/';
  };

  // Allow pages to rename current session
  window.renameCurrentSession = function (newName) {
    const sessions = getSessions();
    const currentId = getCurrentId();
    for (var i = 0; i < sessions.length; i++) {
      if (sessions[i].id === currentId) {
        sessions[i].name = newName;
        break;
      }
    }
    saveSessions(sessions);
    renderSidebar(sessions, currentId);
  };

  // Run on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
