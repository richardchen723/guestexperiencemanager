(function () {
    'use strict';

    const endpoints = {
        list: '/admin/api/api-keys',
        create: '/admin/api/api-keys'
    };

    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('createApiKeyForm').addEventListener('submit', createApiKey);
        document.getElementById('copyApiKeyButton').addEventListener('click', copyNewApiKey);
        loadApiKeys();
    });

    async function loadApiKeys() {
        const loading = document.getElementById('apiKeysLoading');
        const error = document.getElementById('apiKeysError');
        const empty = document.getElementById('apiKeysEmpty');
        const list = document.getElementById('apiKeysList');

        loading.hidden = false;
        error.hidden = true;
        empty.hidden = true;
        list.replaceChildren();

        try {
            const response = await fetch(endpoints.list, { headers: { Accept: 'application/json' } });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || 'Unable to load API keys');

            const keys = Array.isArray(payload) ? payload : [];
            document.getElementById('activeKeyCount').textContent = String(keys.filter(key => key.is_active).length);
            document.getElementById('totalKeyCount').textContent = String(keys.length);
            loading.hidden = true;

            if (keys.length === 0) {
                empty.hidden = false;
                return;
            }

            keys.forEach(key => list.appendChild(renderApiKey(key)));
        } catch (loadError) {
            loading.hidden = true;
            error.textContent = loadError.message;
            error.hidden = false;
        }
    }

    function renderApiKey(key) {
        const row = document.createElement('article');
        row.className = 'api-key-row';

        const identity = document.createElement('div');
        identity.className = 'api-key-identity';
        const name = document.createElement('strong');
        name.textContent = key.name || 'Unnamed key';
        const id = document.createElement('small');
        id.textContent = `Credential #${key.api_key_id}`;
        identity.append(name, id);

        const prefix = document.createElement('code');
        prefix.className = 'api-key-prefix';
        prefix.textContent = `${key.key_prefix || 'hk_'}${'•'.repeat(14)}`;
        prefix.setAttribute('aria-label', `Key prefix ${key.key_prefix || 'unknown'}`);

        const scopes = Array.isArray(key.scopes) ? key.scopes : ['*'];
        const access = document.createElement('span');
        access.className = `api-key-access${scopes.includes('*') ? ' is-full' : ''}`;
        access.textContent = scopes.includes('*') ? 'Full API' : 'Guest Issues · read only';

        const created = createMeta('Created', formatDateTime(key.created_at));
        const lastUsed = createMeta('Last used', formatDateTime(key.last_used_at));
        lastUsed.classList.add('api-key-last-used');

        const status = document.createElement('span');
        status.className = `api-key-status${key.is_active ? '' : ' is-revoked'}`;
        status.textContent = key.is_active ? 'Active' : 'Revoked';

        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'api-key-delete-button';
        remove.textContent = 'Delete';
        remove.addEventListener('click', () => deleteApiKey(key, remove));

        row.append(identity, prefix, access, created, lastUsed, status, remove);
        return row;
    }

    function createMeta(label, value) {
        const meta = document.createElement('div');
        meta.className = 'api-key-meta';
        const strong = document.createElement('strong');
        strong.textContent = value;
        const small = document.createElement('small');
        small.textContent = label;
        meta.append(strong, small);
        return meta;
    }

    async function createApiKey(event) {
        event.preventDefault();
        const nameInput = document.getElementById('apiKeyName');
        const accessInput = document.getElementById('apiKeyAccess');
        const button = document.getElementById('createApiKeyButton');
        const error = document.getElementById('createApiKeyError');
        const name = nameInput.value.trim();
        if (!name) return;

        button.disabled = true;
        button.textContent = 'Creating…';
        error.hidden = true;

        try {
            const response = await fetch(endpoints.create, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
                body: JSON.stringify({ name, access: accessInput.value })
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || 'Unable to create API key');

            const panel = document.getElementById('newApiKeyPanel');
            const value = document.getElementById('newApiKeyValue');
            value.value = payload.api_key;
            const scopes = Array.isArray(payload.scopes) ? payload.scopes : ['*'];
            document.getElementById('newApiKeyWarning').innerHTML = scopes.includes('*')
                ? '<strong>Keep this credential private.</strong> It has full read and write API access.'
                : '<strong>Keep this credential private.</strong> It can read the PII-minimized Guest Issues API only.';
            panel.hidden = false;
            nameInput.value = '';
            panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            await loadApiKeys();
        } catch (createError) {
            error.textContent = createError.message;
            error.hidden = false;
        } finally {
            button.disabled = false;
            button.textContent = 'Create key';
        }
    }

    async function copyNewApiKey() {
        const input = document.getElementById('newApiKeyValue');
        const button = document.getElementById('copyApiKeyButton');
        if (!input.value) return;

        try {
            await navigator.clipboard.writeText(input.value);
        } catch (_error) {
            input.focus();
            input.select();
            document.execCommand('copy');
        }

        button.textContent = 'Copied';
        window.setTimeout(() => { button.textContent = 'Copy key'; }, 1800);
    }

    async function deleteApiKey(key, button) {
        const label = key.name || `Credential #${key.api_key_id}`;
        const confirmed = window.confirm(`Delete “${label}”? This immediately invalidates the key and cannot be undone.`);
        if (!confirmed) return;

        button.disabled = true;
        button.textContent = 'Deleting…';

        try {
            const response = await fetch(`/admin/api/api-keys/${key.api_key_id}`, {
                method: 'DELETE',
                headers: { Accept: 'application/json' }
            });
            const payload = await response.json();
            if (!response.ok) throw new Error(payload.error || 'Unable to delete API key');
            await loadApiKeys();
        } catch (deleteError) {
            window.alert(deleteError.message);
            button.disabled = false;
            button.textContent = 'Delete';
        }
    }

    function formatDateTime(value) {
        if (!value) return 'Never';
        const date = new Date(value);
        if (Number.isNaN(date.getTime())) return 'Unknown';
        return date.toLocaleString(undefined, {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: 'numeric',
            minute: '2-digit'
        });
    }
})();
