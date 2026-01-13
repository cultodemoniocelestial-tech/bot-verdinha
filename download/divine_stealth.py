"""
Divine Stealth - Nível Máximo de Anti-Detecção
Implementa todas as técnicas conhecidas para evitar detecção de bots
"""

import random
import time
import math

# ============================================
# JavaScript de Stealth Divino
# ============================================

DIVINE_STEALTH_JS = """
() => {
    // ========================================
    // 1. WEBDRIVER DETECTION BYPASS
    // ========================================
    
    // Remove navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });
    
    // Remove todas as propriedades de automação
    delete navigator.__proto__.webdriver;
    
    // Remover window.cdc (ChromeDriver)
    const cdcProps = Object.getOwnPropertyNames(window).filter(p => p.match(/cdc_|__cdc/));
    cdcProps.forEach(prop => {
        try { delete window[prop]; } catch(e) {}
    });
    
    // ========================================
    // 2. CHROME RUNTIME SPOOFING
    // ========================================
    
    window.chrome = {
        app: {
            isInstalled: false,
            InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' },
            RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }
        },
        runtime: {
            OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' },
            OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' },
            PlatformArch: { ARM: 'arm', ARM64: 'arm64', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' },
            PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' },
            RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' },
            connect: function() { return { onMessage: { addListener: function() {} }, postMessage: function() {}, disconnect: function() {} }; },
            sendMessage: function() {}
        },
        csi: function() { return {}; },
        loadTimes: function() {
            return {
                commitLoadTime: Date.now() / 1000 - Math.random() * 5,
                connectionInfo: 'h2',
                finishDocumentLoadTime: Date.now() / 1000 - Math.random() * 2,
                finishLoadTime: Date.now() / 1000 - Math.random(),
                firstPaintAfterLoadTime: 0,
                firstPaintTime: Date.now() / 1000 - Math.random() * 3,
                navigationType: 'Other',
                npnNegotiatedProtocol: 'h2',
                requestTime: Date.now() / 1000 - Math.random() * 10,
                startLoadTime: Date.now() / 1000 - Math.random() * 8,
                wasAlternateProtocolAvailable: false,
                wasFetchedViaSpdy: true,
                wasNpnNegotiated: true
            };
        }
    };
    
    // ========================================
    // 3. PLUGINS SPOOFING
    // ========================================
    
    const pluginData = [
        { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
        { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
        { name: 'Native Client', filename: 'internal-nacl-plugin', description: '' }
    ];
    
    const pluginArray = pluginData.map(p => {
        const plugin = Object.create(Plugin.prototype);
        Object.defineProperties(plugin, {
            name: { value: p.name, enumerable: true },
            filename: { value: p.filename, enumerable: true },
            description: { value: p.description, enumerable: true },
            length: { value: 1, enumerable: true }
        });
        return plugin;
    });
    
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = Object.create(PluginArray.prototype);
            pluginArray.forEach((p, i) => { arr[i] = p; });
            Object.defineProperty(arr, 'length', { value: pluginArray.length });
            arr.item = (i) => pluginArray[i] || null;
            arr.namedItem = (n) => pluginArray.find(p => p.name === n) || null;
            arr.refresh = () => {};
            return arr;
        },
        configurable: true
    });
    
    // ========================================
    // 4. LANGUAGES SPOOFING
    // ========================================
    
    Object.defineProperty(navigator, 'languages', {
        get: () => ['pt-BR', 'pt', 'en-US', 'en'],
        configurable: true
    });
    
    Object.defineProperty(navigator, 'language', {
        get: () => 'pt-BR',
        configurable: true
    });
    
    // ========================================
    // 5. PERMISSIONS SPOOFING
    // ========================================
    
    const originalQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications' ?
            Promise.resolve({ state: Notification.permission }) :
            originalQuery(parameters)
    );
    
    // ========================================
    // 6. WEBGL SPOOFING
    // ========================================
    
    const getParameterProxyHandler = {
        apply: function(target, thisArg, args) {
            const param = args[0];
            const gl = thisArg;
            
            // UNMASKED_VENDOR_WEBGL
            if (param === 37445) {
                return 'Google Inc. (NVIDIA)';
            }
            // UNMASKED_RENDERER_WEBGL
            if (param === 37446) {
                return 'ANGLE (NVIDIA, NVIDIA GeForce GTX 1080 Direct3D11 vs_5_0 ps_5_0, D3D11)';
            }
            
            return Reflect.apply(target, thisArg, args);
        }
    };
    
    try {
        const canvas = document.createElement('canvas');
        const gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
        if (gl) {
            const originalGetParameter = gl.__proto__.getParameter;
            gl.__proto__.getParameter = new Proxy(originalGetParameter, getParameterProxyHandler);
        }
        
        const gl2 = canvas.getContext('webgl2');
        if (gl2) {
            const originalGetParameter2 = gl2.__proto__.getParameter;
            gl2.__proto__.getParameter = new Proxy(originalGetParameter2, getParameterProxyHandler);
        }
    } catch(e) {}
    
    // ========================================
    // 7. CANVAS FINGERPRINT PROTECTION
    // ========================================
    
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        if (this.width === 0 || this.height === 0) {
            return originalToDataURL.apply(this, arguments);
        }
        
        const context = this.getContext('2d');
        if (context) {
            const imageData = context.getImageData(0, 0, this.width, this.height);
            for (let i = 0; i < imageData.data.length; i += 4) {
                // Adicionar ruído imperceptível
                imageData.data[i] = imageData.data[i] ^ (Math.random() > 0.5 ? 1 : 0);
            }
            context.putImageData(imageData, 0, 0);
        }
        
        return originalToDataURL.apply(this, arguments);
    };
    
    const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function() {
        const imageData = originalGetImageData.apply(this, arguments);
        for (let i = 0; i < imageData.data.length; i += 4) {
            imageData.data[i] = imageData.data[i] ^ (Math.random() > 0.5 ? 1 : 0);
        }
        return imageData;
    };
    
    // ========================================
    // 8. AUDIO FINGERPRINT PROTECTION
    // ========================================
    
    try {
        const originalGetChannelData = AudioBuffer.prototype.getChannelData;
        AudioBuffer.prototype.getChannelData = function() {
            const data = originalGetChannelData.apply(this, arguments);
            for (let i = 0; i < data.length; i += 100) {
                data[i] = data[i] + (Math.random() * 0.0001 - 0.00005);
            }
            return data;
        };
    } catch(e) {}
    
    // ========================================
    // 9. HARDWARE CONCURRENCY
    // ========================================
    
    Object.defineProperty(navigator, 'hardwareConcurrency', {
        get: () => 8,
        configurable: true
    });
    
    // ========================================
    // 10. DEVICE MEMORY
    // ========================================
    
    Object.defineProperty(navigator, 'deviceMemory', {
        get: () => 8,
        configurable: true
    });
    
    // ========================================
    // 11. PLATFORM SPOOFING
    // ========================================
    
    Object.defineProperty(navigator, 'platform', {
        get: () => 'Win32',
        configurable: true
    });
    
    // ========================================
    // 12. VENDOR SPOOFING
    // ========================================
    
    Object.defineProperty(navigator, 'vendor', {
        get: () => 'Google Inc.',
        configurable: true
    });
    
    // ========================================
    // 13. CONNECTION SPOOFING
    // ========================================
    
    Object.defineProperty(navigator, 'connection', {
        get: () => ({
            effectiveType: '4g',
            rtt: 50,
            downlink: 10,
            saveData: false
        }),
        configurable: true
    });
    
    // ========================================
    // 14. BATTERY API SPOOFING
    // ========================================
    
    if (navigator.getBattery) {
        navigator.getBattery = () => Promise.resolve({
            charging: true,
            chargingTime: 0,
            dischargingTime: Infinity,
            level: 1,
            addEventListener: () => {},
            removeEventListener: () => {}
        });
    }
    
    // ========================================
    // 15. SCREEN PROPERTIES
    // ========================================
    
    Object.defineProperty(screen, 'colorDepth', { get: () => 24, configurable: true });
    Object.defineProperty(screen, 'pixelDepth', { get: () => 24, configurable: true });
    
    // ========================================
    // 16. TIMEZONE SPOOFING
    // ========================================
    
    const originalDateTimeFormat = Intl.DateTimeFormat;
    Intl.DateTimeFormat = function(locale, options) {
        const formatter = new originalDateTimeFormat(locale || 'pt-BR', options);
        return formatter;
    };
    Intl.DateTimeFormat.prototype = originalDateTimeFormat.prototype;
    
    // ========================================
    // 17. IFRAME DETECTION BYPASS
    // ========================================
    
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function() {
            return window;
        }
    });
    
    // ========================================
    // 18. HEADLESS DETECTION BYPASS
    // ========================================
    
    // Remover indicadores de headless
    Object.defineProperty(navigator, 'userAgent', {
        get: () => 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        configurable: true
    });
    
    // ========================================
    // 19. MEDIA DEVICES SPOOFING
    // ========================================
    
    if (navigator.mediaDevices && navigator.mediaDevices.enumerateDevices) {
        const originalEnumerateDevices = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
        navigator.mediaDevices.enumerateDevices = async function() {
            const devices = await originalEnumerateDevices();
            if (devices.length === 0) {
                return [
                    { deviceId: 'default', kind: 'audioinput', label: '', groupId: 'default' },
                    { deviceId: 'default', kind: 'audiooutput', label: '', groupId: 'default' },
                    { deviceId: 'default', kind: 'videoinput', label: '', groupId: 'default' }
                ];
            }
            return devices;
        };
    }
    
    // ========================================
    // 20. FUNCTION TO STRING SPOOFING
    // ========================================
    
    const originalToString = Function.prototype.toString;
    Function.prototype.toString = function() {
        if (this === navigator.permissions.query) {
            return 'function query() { [native code] }';
        }
        if (this === HTMLCanvasElement.prototype.toDataURL) {
            return 'function toDataURL() { [native code] }';
        }
        return originalToString.call(this);
    };
    
    // ========================================
    // 21. CONSOLE LOG SPOOFING
    // ========================================
    
    // Esconder que estamos em modo automatizado
    const originalConsoleLog = console.log;
    console.log = function() {
        const args = Array.from(arguments);
        const hasAutomation = args.some(arg => 
            typeof arg === 'string' && 
            (arg.includes('automation') || arg.includes('webdriver') || arg.includes('selenium'))
        );
        if (!hasAutomation) {
            originalConsoleLog.apply(console, arguments);
        }
    };
    
    // ========================================
    // 22. DOCUMENT PROPERTIES
    // ========================================
    
    Object.defineProperty(document, 'hidden', { get: () => false, configurable: true });
    Object.defineProperty(document, 'visibilityState', { get: () => 'visible', configurable: true });
    
    // ========================================
    // FINALIZAÇÃO
    // ========================================
    
    console.log('[Divine Stealth] Proteção ativada com sucesso!');
}
"""

# ============================================
# Funções de Movimento Humanizado
# ============================================

def generate_human_mouse_path(start_x, start_y, end_x, end_y, steps=None):
    """Gera um caminho de mouse humanizado usando curvas de Bezier"""
    if steps is None:
        distance = math.sqrt((end_x - start_x)**2 + (end_y - start_y)**2)
        steps = max(10, int(distance / 10))
    
    # Pontos de controle para curva de Bezier
    ctrl1_x = start_x + (end_x - start_x) * 0.3 + random.uniform(-50, 50)
    ctrl1_y = start_y + (end_y - start_y) * 0.1 + random.uniform(-30, 30)
    ctrl2_x = start_x + (end_x - start_x) * 0.7 + random.uniform(-50, 50)
    ctrl2_y = start_y + (end_y - start_y) * 0.9 + random.uniform(-30, 30)
    
    path = []
    for i in range(steps + 1):
        t = i / steps
        
        # Curva de Bezier cúbica
        x = (1-t)**3 * start_x + 3*(1-t)**2*t * ctrl1_x + 3*(1-t)*t**2 * ctrl2_x + t**3 * end_x
        y = (1-t)**3 * start_y + 3*(1-t)**2*t * ctrl1_y + 3*(1-t)*t**2 * ctrl2_y + t**3 * end_y
        
        # Adicionar micro-tremores humanos
        x += random.uniform(-2, 2)
        y += random.uniform(-2, 2)
        
        path.append((int(x), int(y)))
    
    return path

def human_type(page, selector, text, min_delay=0.05, max_delay=0.15):
    """Digita texto de forma humanizada - versão estável"""
    element = page.locator(selector)
    
    # Limpar campo primeiro
    element.click()
    time.sleep(random.uniform(0.1, 0.3))
    
    # Usar fill() que é mais estável, mas com delay humanizado antes
    # Simula o tempo que uma pessoa levaria para digitar
    typing_time = len(text) * random.uniform(min_delay, max_delay)
    time.sleep(min(typing_time, 2.0))  # Máximo 2 segundos de "digitação"
    
    # Preencher o campo
    element.fill(text)
    
    # Delay após digitar (como se verificando o que digitou)
    time.sleep(random.uniform(0.2, 0.5))

def human_click(page, selector):
    """Clica de forma humanizada com movimento de mouse"""
    element = page.locator(selector)
    box = element.bounding_box()
    
    if box:
        # Ponto aleatório dentro do elemento
        target_x = box['x'] + box['width'] * random.uniform(0.3, 0.7)
        target_y = box['y'] + box['height'] * random.uniform(0.3, 0.7)
        
        # Posição atual do mouse (simulada)
        current_x = random.randint(0, 1920)
        current_y = random.randint(0, 1080)
        
        # Gerar caminho humanizado
        path = generate_human_mouse_path(current_x, current_y, target_x, target_y)
        
        # Mover o mouse pelo caminho
        for x, y in path:
            page.mouse.move(x, y)
            time.sleep(random.uniform(0.001, 0.01))
        
        # Pequena pausa antes do clique
        time.sleep(random.uniform(0.05, 0.15))
        
        # Clique com duração variável
        page.mouse.down()
        time.sleep(random.uniform(0.05, 0.12))
        page.mouse.up()
    else:
        element.click()

def apply_divine_stealth(target):
    """Aplica o stealth divino.

    IMPORTANTE:
    - Para evitar detecção em sites que checam propriedades logo no início, instalamos
      o script como init script (executa em TODAS as navegações antes do JS do site).
    - Também tentamos aplicar no documento atual (best-effort).
    """
    js_init = f"({DIVINE_STEALTH_JS})();"
    try:
        # Funciona tanto para Page quanto para BrowserContext (ambos têm add_init_script)
        target.add_init_script(js_init)
    except Exception:
        pass

    # Best-effort: aplicar no documento atual (só Page tem evaluate)
    try:
        if hasattr(target, 'evaluate'):
            target.evaluate(DIVINE_STEALTH_JS)
    except Exception:
        pass

    return True


def get_stealth_context_options():
    """Retorna opções de contexto com stealth, sem forçar headers inconsistentes.

    NOTA: 'extra_http_headers' com 'Sec-Fetch-*' / 'Upgrade-Insecure-Requests' etc.
    aplicados globalmente pode quebrar o site (esses headers variam por tipo de request).
    Deixe o Chromium/Playwright gerenciar isso.
    """
    return {
        'viewport': {'width': 1920, 'height': 1080},
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'locale': 'pt-BR',
        'timezone_id': 'America/Sao_Paulo',
        'geolocation': {'latitude': -23.5505, 'longitude': -46.6333},
        'permissions': ['geolocation'],
        'color_scheme': 'light',
        'device_scale_factor': 1,
        'is_mobile': False,
        'has_touch': False,
        'java_script_enabled': True,
        # Evite bypass_csp/ignore_https_errors a menos que seja necessário.
        'bypass_csp': False,
        'ignore_https_errors': False,
        # Se você quiser MUITO um header fixo, deixe só Accept-Language:
        'extra_http_headers': {
            'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
        },
    }

def get_stealth_browser_args():
    """Retorna argumentos do navegador com máximo stealth"""
    return [
        '--disable-blink-features=AutomationControlled',
        '--disable-dev-shm-usage',
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-infobars',
        '--disable-extensions',
        '--disable-gpu',
        '--disable-software-rasterizer',
        '--disable-background-networking',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-breakpad',
        '--disable-component-extensions-with-background-pages',
        '--disable-component-update',
        '--disable-default-apps',
        '--disable-domain-reliability',
        '--disable-features=TranslateUI',
        '--disable-hang-monitor',
        '--disable-ipc-flooding-protection',
        '--disable-popup-blocking',
        '--disable-prompt-on-repost',
        '--disable-renderer-backgrounding',
        '--disable-sync',
        '--enable-features=NetworkService,NetworkServiceInProcess',
        '--force-color-profile=srgb',
        '--metrics-recording-only',
        '--no-first-run',
        '--password-store=basic',
        '--use-mock-keychain',
        '--export-tagged-pdf',
        '--window-size=1920,1080',
        '--start-maximized',
    ]

def random_scroll(page):
    """Faz scroll aleatório para simular comportamento humano"""
    scroll_amount = random.randint(100, 500)
    direction = random.choice(['up', 'down'])
    
    if direction == 'down':
        page.mouse.wheel(0, scroll_amount)
    else:
        page.mouse.wheel(0, -scroll_amount)
    
    time.sleep(random.uniform(0.5, 1.5))

def random_mouse_movement(page):
    """Faz movimentos aleatórios de mouse"""
    for _ in range(random.randint(2, 5)):
        x = random.randint(100, 1800)
        y = random.randint(100, 900)
        page.mouse.move(x, y)
        time.sleep(random.uniform(0.1, 0.3))

def human_delay(min_sec=0.5, max_sec=2.0):
    """Delay humanizado"""
    time.sleep(random.uniform(min_sec, max_sec))
