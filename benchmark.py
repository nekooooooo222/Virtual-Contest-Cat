import time
import math
import os
from aiohttp import web

def run_benchmark():
    log = []
    log.append("テスト2. 文字列処理・リスト操作")
    start_time = time.time()
    dummy_html = "<tr><td>100</td><td>AC</td><td>10:00</td></tr>" * 200000
    dummy_html = dummy_html.replace("td>", "div>")
    rows = dummy_html.split("<tr>")
    str_time = time.time() - start_time
    log.append(f"結果: {str_time:.3f} 秒 ")
    return "\n".join(log)

async def handle_benchmark(request):
    result_text = run_benchmark()
    print(result_text)
    return web.Response(text=result_text, content_type='text/plain', charset='utf-8')

app = web.Application()
app.add_routes([web.get('/', handle_benchmark)])

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f"ベンチマークサーバーをポート {port} で起動しました。")
    web.run_app(app, host='0.0.0.0', port=port)
