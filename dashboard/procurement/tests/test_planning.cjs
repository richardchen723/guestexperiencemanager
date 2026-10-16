const test = require('node:test');
const assert = require('node:assert/strict');
const planning = require('../../static/js/procurement-planning.js');
const paper = (id,listing_id,quantity,extra={}) => ({id,listing_id,quantity,catalog_id:3,name:'Toilet paper',unit:'rolls',category:'paper',allocations:[],...extra});
const need = (id,extra={}) => ({...paper(id,101,12),to_order:12,property:'Sample property',...extra});

test('reopening a grid preserves unknown quantities and omitted not-needed cells', () => {
    const previous = [paper(1,101,null),paper(2,102,12),{...paper(3,102,1),catalog_id:4,name:'Detergent',unit:'bottles'}];
    const draft = planning.requestGrid(previous,[101,102]);
    assert.equal(draft.columns[0].values[101].quantity,'');
    assert.equal(draft.columns[1].values[101].quantity,'0');
    assert.deepEqual(planning.requestChanges(draft,previous),[]);
    draft.columns[0].values[101].quantity = '6';
    assert.deepEqual(planning.requestChanges(draft,previous),[{catalog_id:3,name:'Toilet paper',unit:'rolls',category:'paper',quantity:'6',listing_id:101,id:1}]);
});

test('a reserved requirement is retained while a new property or supply is added', () => {
    const previous = [paper(1,101,12,{allocations:[{id:88}]})];
    const draft = planning.requestGrid(previous,[101]);
    draft.ids.push(102);
    draft.columns[0].values[102] = {quantity:'3'};
    assert.deepEqual(planning.requestChanges(draft,previous),[{catalog_id:3,name:'Toilet paper',unit:'rolls',category:'paper',quantity:'3',listing_id:102}]);
    assert.equal(draft.columns[0].values[101].locked,true);
});

test('not-needed rows are omitted and removed unpurchased rows are explicit', () => {
    const previous = [paper(1,101,12),paper(2,102,12)];
    const draft = planning.requestGrid(previous,[101,102]);
    draft.columns[0].values[102].quantity = '0';
    assert.deepEqual(planning.requestChanges(draft,previous),[{id:2,remove:true}]);
});

test('duplicate same-product requirements at one property retain separate item IDs', () => {
    const previous = [paper(1,101,12),paper(2,101,6)];
    const draft = planning.requestGrid(previous,[101]);
    assert.equal(draft.columns.length,2);
    assert.deepEqual(planning.requestChanges(draft,previous),[]);
});

test('partial purchases combine requests and keep the exact original allocations', () => {
    const lines = planning.combinePurchases([need(1),need(2,{listing_id:102,ticket_id:9})],{1:{quantity:'3',location_id:7},2:{quantity:'6',location_id:7}},[7]);
    assert.equal(lines.length,1);
    assert.equal(lines[0].quantity,9);
    assert.deepEqual(lines[0].allocations,[{item_id:1,quantity:3},{item_id:2,quantity:6}]);
});

test('separate addresses and different product specifications never merge', () => {
    for (const difference of [{name:'Premium toilet paper'},{unit:'packs'},{catalog_id:4},{category:'different'}]) {
        assert.equal(planning.combinePurchases([need(1),need(2,difference)],{1:{quantity:3,location_id:7},2:{quantity:3,location_id:7}},[7]).length,2);
    }
    const lines = planning.combinePurchases([need(1),need(2)],{1:{quantity:12,location_id:7},2:{quantity:12,location_id:8}},[7,8]);
    assert.equal(lines.length,2);
    assert.deepEqual(lines.map(l => planning.wholePackQuantity(l.quantity,l.quantity,24)),[24,24]);
});

test('zero, excessive, invalid, or overprecise partial purchases are rejected', () => {
    for (const amount of [0,-1,13,Infinity,'NaN','0.0001','']) assert.throws(() => planning.combinePurchases([need(1)],{1:{quantity:amount,location_id:7}},[7]),/quantity/);
    assert.throws(() => planning.combinePurchases([need(1)],{1:{quantity:3,location_id:''}},[7]),/address/);
    assert.throws(() => planning.combinePurchases([need(1)],{},[7]),/quantity/);
});

test('fractional base units keep exact thousandths when combined', () => {
    const lines = planning.combinePurchases([need(1),need(2)],{1:{quantity:'0.1',location_id:7},2:{quantity:'0.2',location_id:7}},[7]);
    assert.equal(lines[0].quantity,0.3);
});

test('pack rounding covers allocations, preserves desired extras and avoids decimal drift', () => {
    assert.equal(planning.wholePackQuantity(12,12,24),24);
    assert.equal(planning.wholePackQuantity(24,25,24),48);
    assert.equal(planning.wholePackQuantity(49,12,24),72);
    assert.equal(planning.wholePackQuantity(0.3,0.3,0.1),0.3);
    assert.throws(() => planning.wholePackQuantity(12,12,0),/pack size/);
});
