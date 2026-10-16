/* Pure planning rules shared by the forms and dependency-free regression tests.
 * Saved quantities and reservations are always revalidated by the server. */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) module.exports = factory();
    else root.ProcurementPlanning = factory();
})(typeof window !== 'undefined' ? window : this, function () {
    'use strict';
    function requestGrid(items = [], listingIds = []) {
        const draft = {ids:[...listingIds],columns:[],sequence:0};
        for (const item of items) {
            let column = draft.columns.find(c => String(c.catalog_id || '') === String(item.catalog_id || '') && c.name === item.name && c.unit === item.unit && c.category === item.category && !c.values[item.listing_id || 0]);
            if (!column) {
                column = {key:++draft.sequence,catalog_id:item.catalog_id,name:item.name,unit:item.unit,category:item.category,values:{}};
                draft.columns.push(column);
            }
            column.values[item.listing_id || 0] = {id:item.id,quantity:item.quantity ?? '',locked:!!item.allocations?.length || !!item.cancelled};
        }
        // Missing pairs were deliberately omitted, rather than left unknown.
        for (const column of draft.columns) for (const id of draft.ids) {
            if (!column.values[id]) column.values[id] = {quantity:'0',id:null,locked:false};
        }
        return draft;
    }
    function requestChanges(draft, previous = []) {
        const items = [], retained = new Set();
        for (const column of draft.columns) for (const id of draft.ids.length ? draft.ids : [0]) {
            const v = column.values[id] || {}, old = previous.find(i => i.id === v.id);
            if (v.quantity !== '' && v.quantity != null && Number(v.quantity) === 0) continue;
            if (v.id) retained.add(v.id);
            if (v.locked) continue;
            const row = {catalog_id:column.catalog_id || null,name:column.name,unit:column.unit,category:column.category,quantity:v.quantity === '' ? null : v.quantity,listing_id:id || null};
            if (old && String(old.quantity ?? '') === String(row.quantity ?? '') && old.name === row.name && old.unit === row.unit && old.category === row.category && (old.catalog_id || null) === (Number(row.catalog_id) || null) && old.listing_id === row.listing_id) continue;
            items.push({...row,...(v.id ? {id:v.id} : {})});
        }
        for (const old of previous) if (!retained.has(old.id)) items.push({id:old.id,remove:true});
        return items;
    }
    function combinePurchases(items, choices, locationIds) {
        if (!items.length) throw new Error('Select supplies to purchase first.');
        const groups = new Map();
        for (const item of items) {
            const choice = choices[item.id], amount = Number(choice?.quantity);
            if (!Number.isFinite(amount) || amount <= 0 || amount > item.to_order || Math.abs(amount * 1000 - Math.round(amount * 1000)) > 0.00001) {
                throw new Error(`Choose a quantity up to ${item.to_order} ${item.unit} for ${item.property || item.label}.`);
            }
            if (!locationIds.includes(Number(choice.location_id))) throw new Error('Choose a verified receiving address for every selected supply.');
            const key = JSON.stringify([Number(choice.location_id),item.catalog_id,item.name.toLowerCase(),item.unit.toLowerCase(),item.category]);
            if (!groups.has(key)) groups.set(key,{catalog_id:item.catalog_id,name:item.name,unit:item.unit,quantity:0,pack_size:1,location_id:Number(choice.location_id),allocations:[]});
            const line = groups.get(key);
            line.quantity = Math.round((line.quantity + amount) * 1000) / 1000;
            line.allocations.push({item_id:item.id,quantity:amount});
            if (line.allocations.length > 100) throw new Error('Prepare up to 100 quantities of the same product per purchase line. Split this selection into separate drafts.');
        }
        if (groups.size > 100) throw new Error('Prepare up to 100 purchase lines at a time.');
        return [...groups.values()];
    }
    function wholePackQuantity(amount, allocated, pack) {
        amount = Math.max(Number(amount),Number(allocated));
        pack = Number(pack);
        if (!Number.isFinite(amount) || !Number.isFinite(pack) || pack <= 0 || Math.round(pack * 1000) === 0) throw new Error('Enter a valid pack size and quantity first.');
        return Math.ceil(Math.round(amount * 1000) / Math.round(pack * 1000)) * Math.round(pack * 1000) / 1000;
    }
    return {requestGrid,requestChanges,combinePurchases,wholePackQuantity};
});
