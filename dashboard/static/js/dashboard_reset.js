// =============================================================================
// FORCE STOP / RESET HANDLER
// Add this to your dashboard.js or main JavaScript file
// =============================================================================

/**
 * Force Stop / Reset functionality
 * 
 * Features:
 * - Confirmation dialog before destructive action
 * - Archives session before clearing
 * - Hard-kills all running processes
 * - Clears all state, resets equity to $10,000
 * - Shows archive list for downloading past sessions
 */

// --- Force Reset Button Handler ---
async function handleForceReset() {
    // Step 1: Confirmation Dialog
    const confirmed = confirm(
        "⚠️ FORCE STOP / RESET\n\n" +
        "This will:\n" +
        "• Hard-kill any running bot processes\n" +
        "• Archive your current trade history\n" +
        "• Clear all open positions\n" +
        "• Reset account equity to $10,000\n\n" +
        "Your trade history will be saved to an archive file before clearing.\n\n" +
        "Continue?"
    );
    
    if (!confirmed) {
        console.log("Force reset cancelled by user");
        return;
    }
    
    // Step 2: Show loading state
    const resetBtn = document.getElementById('forceResetBtn');
    if (resetBtn) {
        resetBtn.disabled = true;
        resetBtn.textContent = 'Resetting...';
        resetBtn.classList.add('loading');
    }
    
    try {
        // Step 3: Call the reset endpoint
        const response = await fetch('/api/force-reset', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
        });
        
        const result = await response.json();
        
        // Step 4: Handle response
        if (result.success) {
            // Build detailed success message
            let message = "✅ Reset Complete\n\n";
            
            // Processes killed
            if (result.processes_killed && result.processes_killed.length > 0) {
                message += "Processes stopped:\n";
                result.processes_killed.forEach(p => {
                    message += `  • PID ${p.pid}: SIGTERM=${p.sigterm}, SIGKILL=${p.sigkill}\n`;
                });
                message += "\n";
            } else {
                message += "No running processes found.\n\n";
            }
            
            // Archive info
            if (result.archive && result.archive.saved) {
                const session = result.archive.session || {};
                message += "📁 Archive saved:\n";
                message += `  File: ${result.archive.archive_name}.json\n`;
                message += `  Trades archived: ${session.total_trades || 0}\n`;
                message += `  Final equity: $${(session.final_equity || 0).toLocaleString()}\n`;
                message += `  Total P&L: $${(session.total_realized_pnl || 0).toLocaleString()}\n`;
                message += `  Win rate: ${session.win_rate_pct || 0}%\n\n`;
            }
            
            // State cleared
            if (result.state_cleared) {
                message += "State cleared:\n";
                message += `  • PID file: ${result.state_cleared.pid_deleted ? '✓' : '✗'}\n`;
                message += `  • Strategy state: ${result.state_cleared.state_deleted ? '✓' : '✗'}\n`;
                message += `  • Logs cleared: ${result.state_cleared.logs_cleared ? '✓' : '✗'}\n`;
                message += `  • DB reset: ${result.state_cleared.db_reset ? '✓' : '✗'}\n`;
                message += `  • Equity reset: ${result.state_cleared.equity_reset ? '✓' : '✗'}\n`;
                if (result.state_cleared.open_positions_closed) {
                    message += `  • Open positions closed: ${result.state_cleared.open_positions_closed}\n`;
                }
            }
            
            message += `\n💰 Account equity reset to: $10,000.00`;
            message += `\n🚀 You can now start a fresh session with your new strategy.`;
            
            alert(message);
            
            // Refresh the dashboard UI
            await refreshDashboard();
            
        } else {
            // Error response
            let errorMsg = "❌ Reset Failed\n\n";
            errorMsg += `Error: ${result.error || 'Unknown error'}\n`;
            if (result.traceback) {
                errorMsg += `\nDetails: ${result.traceback.substring(0, 500)}`;
            }
            alert(errorMsg);
        }
        
    } catch (error) {
        console.error("Force reset error:", error);
        alert(`❌ Network error during reset: ${error.message}`);
    } finally {
        // Restore button state
        if (resetBtn) {
            resetBtn.disabled = false;
            resetBtn.textContent = 'Force Stop / Reset';
            resetBtn.classList.remove('loading');
        }
    }
}


// --- Archive Viewer ---
async function loadArchives() {
    const archiveList = document.getElementById('archiveList');
    if (!archiveList) return;
    
    archiveList.innerHTML = '<li>Loading archives...</li>';
    
    try {
        const response = await fetch('/api/archives');
        const result = await response.json();
        
        if (!result.success || !result.archives || result.archives.length === 0) {
            archiveList.innerHTML = '<li class="empty">No archived sessions yet</li>';
            return;
        }
        
        archiveList.innerHTML = '';
        result.archives.forEach(archive => {
            const li = document.createElement('li');
            li.className = 'archive-item';
            
            const date = new Date(archive.created_at);
            const formattedDate = date.toLocaleString();
            const sizeKB = (archive.size_bytes / 1024).toFixed(1);
            
            li.innerHTML = `
                <div class="archive-info">
                    <span class="archive-name">${archive.name}</span>
                    <span class="archive-meta">${formattedDate} • ${sizeKB} KB</span>
                </div>
                <div class="archive-actions">
                    <button onclick="viewArchive('${archive.name}')" class="btn-small">View</button>
                    <a href="/api/archives/download/${archive.name}" class="btn-small" download>Download</a>
                </div>
            `;
            archiveList.appendChild(li);
        });
        
    } catch (error) {
        archiveList.innerHTML = `<li class="error">Error loading archives: ${error.message}</li>`;
    }
}


async function viewArchive(archiveName) {
    try {
        const response = await fetch(`/api/archives/${archiveName}`);
        const result = await response.json();
        
        if (!result.success) {
            alert(`Error: ${result.error}`);
            return;
        }
        
        const archive = result.archive;
        const session = archive.session || {};
        
        let message = `📁 Archive: ${archiveName}\n\n`;
        message += `Archived: ${archive.archived_at}\n`;
        message += `Trades: ${session.total_trades || 0}\n`;
        message += `Closed: ${session.closed_trades || 0}\n`;
        message += `Winners: ${session.winners || 0} | Losers: ${session.losers || 0}\n`;
        message += `Win Rate: ${session.win_rate_pct || 0}%\n`;
        message += `Total P&L: $${(session.total_realized_pnl || 0).toLocaleString()}\n`;
        message += `Final Equity: $${(session.final_equity || 0).toLocaleString()}\n`;
        message += `Return: ${session.total_return_pct || 0}%\n`;
        
        if (archive.open_positions_at_archive && archive.open_positions_at_archive.length > 0) {
            message += `\nOpen positions at archive:\n`;
            archive.open_positions_at_archive.forEach(pos => {
                message += `  • ${pos.symbol} ${pos.direction} @ $${pos.entry_price}\n`;
            });
        }
        
        alert(message);
        
    } catch (error) {
        alert(`Error viewing archive: ${error.message}`);
    }
}


// --- Dashboard Refresh ---
async function refreshDashboard() {
    // Refresh status
    try {
        const statusRes = await fetch('/api/status');
        const status = await statusRes.json();
        updateStatusUI(status);
    } catch (e) {
        console.error("Failed to refresh status:", e);
    }
    
    // Refresh positions
    try {
        const posRes = await fetch('/api/positions');
        const positions = await posRes.json();
        updatePositionsUI(positions);
    } catch (e) {
        console.error("Failed to refresh positions:", e);
    }
    
    // Refresh trades
    try {
        const tradesRes = await fetch('/api/trades');
        const trades = await tradesRes.json();
        updateTradesUI(trades);
    } catch (e) {
        console.error("Failed to refresh trades:", e);
    }
    
    // Refresh balance
    try {
        const balRes = await fetch('/api/balance');
        const balance = await balRes.json();
        updateBalanceUI(balance);
    } catch (e) {
        console.error("Failed to refresh balance:", e);
    }
}


// --- UI Update Helpers (implement these based on your existing HTML) ---
function updateStatusUI(status) {
    const statusEl = document.getElementById('botStatus');
    if (statusEl) {
        statusEl.textContent = status.running ? 'ONLINE' : 'OFFLINE';
        statusEl.className = status.running ? 'status-online' : 'status-offline';
    }
    
    const startBtn = document.getElementById('startBotBtn');
    const stopBtn = document.getElementById('stopBotBtn');
    
    if (startBtn) startBtn.disabled = status.running;
    if (stopBtn) stopBtn.disabled = !status.running;
}

function updatePositionsUI(data) {
    const container = document.getElementById('positionsContainer');
    if (!container) return;
    
    if (!data.positions || data.positions.length === 0) {
        container.innerHTML = '<div class="empty-state">No open positions</div>';
        return;
    }
    
    // Render positions table
    let html = '<table class="positions-table"><thead><tr>';
    html += '<th>Symbol</th><th>Side</th><th>Entry</th><th>Current</th><th>P&L %</th><th>R</th>';
    html += '</tr></thead><tbody>';
    
    data.positions.forEach(pos => {
        const pnlClass = pos.pnl_pct >= 0 ? 'positive' : 'negative';
        html += `<tr>
            <td>${pos.symbol}</td>
            <td class="${pos.direction}">${pos.direction.toUpperCase()}</td>
            <td>$${pos.entry_price.toLocaleString()}</td>
            <td>$${pos.current_price.toLocaleString()}</td>
            <td class="${pnlClass}">${pos.pnl_pct >= 0 ? '+' : ''}${pos.pnl_pct}%</td>
            <td>${pos.r_multiple}R</td>
        </tr>`;
    });
    
    html += '</tbody></table>';
    container.innerHTML = html;
}

function updateTradesUI(data) {
    const container = document.getElementById('tradesContainer');
    if (!container) return;
    
    if (!data.trades || data.trades.length === 0) {
        container.innerHTML = '<div class="empty-state">No trade history</div>';
        return;
    }
    
    let html = '<table class="trades-table"><thead><tr>';
    html += '<th>Symbol</th><th>Side</th><th>Entry</th><th>Exit</th><th>P&L</th><th>R</th><th>Reason</th>';
    html += '</tr></thead><tbody>';
    
    data.trades.forEach(trade => {
        const pnlClass = trade.pnl >= 0 ? 'positive' : 'negative';
        html += `<tr>
            <td>${trade.symbol}</td>
            <td class="${trade.direction}">${trade.direction.toUpperCase()}</td>
            <td>$${trade.entry_price.toLocaleString()}</td>
            <td>$${trade.exit_price ? trade.exit_price.toLocaleString() : '-'}</td>
            <td class="${pnlClass}">${trade.pnl >= 0 ? '+' : ''}$${trade.pnl.toLocaleString()}</td>
            <td>${trade.r_multiple}R</td>
            <td>${trade.exit_reason || 'Open'}</td>
        </tr>`;
    });
    
    html += '</tbody></table>';
    container.innerHTML = html;
}

function updateBalanceUI(data) {
    const equityEl = document.getElementById('accountEquity');
    if (equityEl && data.balance !== undefined) {
        equityEl.textContent = `$${data.balance.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
    }
}


// --- Initialize on page load ---
document.addEventListener('DOMContentLoaded', () => {
    // Attach Force Reset button handler
    const forceResetBtn = document.getElementById('forceResetBtn');
    if (forceResetBtn) {
        forceResetBtn.addEventListener('click', handleForceReset);
    }
    
    // Load archives if archive viewer exists
    const archiveList = document.getElementById('archiveList');
    if (archiveList) {
        loadArchives();
    }
    
    // Initial dashboard refresh
    refreshDashboard();
    
    // Auto-refresh every 30 seconds
    setInterval(refreshDashboard, 30000);
});