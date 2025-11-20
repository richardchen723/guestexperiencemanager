// Test script to verify modal functionality
// This can be run in the browser console to test the modal

function testModal() {
    console.log('=== Testing Modal Display ===');
    
    const modal = document.getElementById('suggestionsModal');
    if (!modal) {
        console.error('❌ Modal element not found in DOM');
        return false;
    }
    
    console.log('✓ Modal element found');
    console.log('Current inline style:', modal.getAttribute('style'));
    console.log('Current computed display:', window.getComputedStyle(modal).display);
    console.log('Current computed visibility:', window.getComputedStyle(modal).visibility);
    console.log('Current computed z-index:', window.getComputedStyle(modal).zIndex);
    console.log('Current computed position:', window.getComputedStyle(modal).position);
    
    // Try to show it
    console.log('\n=== Attempting to show modal ===');
    modal.removeAttribute('style');
    modal.style.setProperty('display', 'flex', 'important');
    modal.style.setProperty('position', 'fixed', 'important');
    modal.style.setProperty('top', '0', 'important');
    modal.style.setProperty('left', '0', 'important');
    modal.style.setProperty('right', '0', 'important');
    modal.style.setProperty('bottom', '0', 'important');
    modal.style.setProperty('z-index', '99999', 'important');
    modal.style.setProperty('background', 'rgba(255, 0, 0, 0.5)', 'important'); // Red background for testing
    modal.style.setProperty('align-items', 'center', 'important');
    modal.style.setProperty('justify-content', 'center', 'important');
    
    // Force reflow
    void modal.offsetHeight;
    
    console.log('After setting styles:');
    console.log('Computed display:', window.getComputedStyle(modal).display);
    console.log('Computed z-index:', window.getComputedStyle(modal).zIndex);
    console.log('Computed position:', window.getComputedStyle(modal).position);
    console.log('Modal offsetHeight:', modal.offsetHeight);
    console.log('Modal offsetWidth:', modal.offsetWidth);
    
    // Check if modal is visible
    const rect = modal.getBoundingClientRect();
    console.log('Modal bounding rect:', rect);
    console.log('Modal visible:', rect.width > 0 && rect.height > 0);
    
    return modal;
}

// Export for use in console
window.testModal = testModal;

