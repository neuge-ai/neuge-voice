const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');

let mainWindow;
let backendProcess;
let backendPort = null;

// Determine if we are in development mode
const isDev = process.env.NODE_ENV === 'development';

function spawnBackend() {
  return new Promise((resolve, reject) => {
    // In production, the backend is bundled in resources/backend.
    // In dev, we expect the backend to be compiled in the root dist/backend folder (from PyInstaller)
    const backendPath = isDev 
      ? path.join(__dirname, '..', '..', 'dist', 'backend')
      : path.join(process.resourcesPath, 'backend');

    console.log(`Starting PyInstaller backend sidecar at: ${backendPath}`);
    
    // Spawn the backend with the dynamic port flag
    backendProcess = spawn(backendPath, ['--dynamic-port']);

    backendProcess.stdout.on('data', (data) => {
      const output = data.toString();
      console.log(`[Backend]: ${output.trim()}`);
      
      // Listen for the specific IPC string
      const match = output.match(/PORT:(\d+)/);
      if (match) {
        backendPort = match[1];
        console.log(`Successfully negotiated dynamic port from backend: ${backendPort}`);
        resolve(backendPort);
      }
    });

    backendProcess.stderr.on('data', (data) => {
      console.error(`[Backend Error]: ${data}`);
    });

    backendProcess.on('close', (code) => {
      console.log(`Backend process exited with code ${code}`);
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
    // Wait for the Vite dev server to be ready (handled by concurrently/wait-on)
    mainWindow.loadURL('http://localhost:5173');
  } else {
    // In production, load the built React files
    mainWindow.loadFile(path.join(__dirname, 'dist', 'index.html'));
  }
}

app.whenReady().then(async () => {
  try {
    await spawnBackend();
  } catch (e) {
    console.error("Failed to spawn backend:", e);
  }

  // Handle IPC request from frontend to get the port
  ipcMain.handle('get-backend-port', () => backendPort);

  createWindow();

  app.on('activate', function () {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', function () {
  if (process.platform !== 'darwin') app.quit();
});

// Ensure backend is killed when Electron exits
app.on('will-quit', () => {
  if (backendProcess) {
    backendProcess.kill();
  }
});
