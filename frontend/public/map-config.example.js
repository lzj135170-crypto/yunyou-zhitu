/* 高德 JS API key（可选）。
 *
 * 复制本文件为同目录下的 map-config.js，填入你自己的高德 JS API key。
 * map-config.js 已被 .gitignore 忽略，不会进入版本库。
 *
 * JS key 本来就是随页面下发的公开 key，真正的限制靠高德控制台的域名白名单；
 * 不要把 Web 服务 key 写在这里，那个 key 只能放在后端 .env。
 *
 * 留空或不创建 map-config.js 时，结果页会退到后端生成的静态地图，
 * 不影响应用启动。
 */
window.TRAVEL_AMAP_JS_KEY = '';
