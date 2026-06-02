const { app, BrowserWindow, ipcMain, systemPreferences } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const {
  DEFAULT_TIMEOUT_MS,
  parsePortLine,
  waitForBackendHealth,
} = require('./managed-backend.cjs');

let mainWindow;
let backendProcess;
let backendPort = null;
let backendStatus = 'idle';

// Determine if we are in development mode
const isDev = process.env.NODE_ENV === 'development';
const repoRoot = path.join(__dirname, '..', '..');
const webAppRoot = path.join(__dirname, '..', 'web');

function setBackendStatus(status) {
  backendStatus = status;
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('backend-status', status);
  }
}

function spawnBackend() {
  return new Promise((resolve, reject) => {
    const backendPath = isDev
      ? path.join(repoRoot, 'dist', 'backend')
      : path.join(process.resourcesPath, 'backend');

    console.error(`Starting managed backend at: ${backendPath}`);
    setBackendStatus('starting');

    let settled = false;
    const timeout = setTimeout(() => {
      if (!settled) {
        settled = true;
        setBackendStatus('failed');
        reject(new Error(`Backend did not emit PORT within ${DEFAULT_TIMEOUT_MS}ms`));
      }
    }, DEFAULT_TIMEOUT_MS);

    backendProcess = spawn(backendPath, ['--dynamic-port']);

    backendProcess.stdout.on('data', (data) => {
      const lines = data.toString().split('\n');
      for (const line of lines) {
        const port = parsePortLine(line);
        if (port && !settled) {
          settled = true;
          clearTimeout(timeout);
          backendPort = String(port);
          waitForBackendHealth('127.0.0.1', port)
            .then(() => {
              setBackendStatus('ready');
              resolve(backendPort);
            })
            .catch((err) => {
              setBackendStatus('failed');
              reject(err);
            });
        }
      }
    });

    backendProcess.stderr.on('data', (data) => {
      console.error(`[Backend]: ${data.toString().trim()}`);
    });

    backendProcess.on('close', (code) => {
      console.error(`Backend process exited with code ${code}`);
      if (backendStatus === 'ready') {
        setBackendStatus('stopped');
      } else if (!settled) {
        settled = true;
        clearTimeout(timeout);
        setBackendStatus('failed');
        reject(new Error(`Backend exited before readiness (code ${code})`));
      }
    });
  });
}

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  mainWindow.setMenu(null);

  if (isDev) {
    mainWindow.loadURL('http://localhost:5173');
  } else {
    mainWindow.loadFile(path.join(webAppRoot, 'dist', 'index.html'));
  }
}

async function ensureMicrophonePermission() {
  if (process.platform !== 'darwin') return true;
  const status = systemPreferences.getMediaAccessStatus('microphone');
  if (status === 'granted') return true;
  if (status === 'denied' || status === 'restricted') return false;
  return await systemPreferences.askForMediaAccess('microphone');
}

app.whenReady().then(async () => {
  const hasMic = await ensureMicrophonePermission();
  if (!hasMic) {
    console.error('Microphone permission denied; voice features may not work on macOS.');
  }

  ipcMain.handle('get-backend-port', () => backendPort);
  ipcMain.handle('get-backend-status', () => backendStatus);
  ipcMain.handle('on-backend-status-ready', async () => backendStatus);

  try {
    await spawnBackend();
  } catch (error) {
    console.error('Failed to start managed backend:', error);
    setBackendStatus('failed');
  }

  createWindow();

  app.on('activate', function () {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', function () {
  if (process.platform !== 'darwin') app.quit();
});

app.on('will-quit', () => {
  if (backendProcess) {
    backendProcess.kill();
  }
});
