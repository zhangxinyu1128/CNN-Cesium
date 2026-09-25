import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'
import { readdir, readFile } from 'node:fs/promises'
import AutoImport from 'unplugin-auto-import/vite'
import cesium from 'vite-plugin-cesium'
import Components from 'unplugin-vue-components/vite'

const projectRoot = resolve(__dirname, '..')
const dataRoot = resolve(projectRoot, 'data')

function localTyphoonDataPlugin(enabled: boolean) {
  return {
    name: 'local-typhoon-data',
    configureServer(server: {
      middlewares: {
        use: (
          handler: (
            req: { url?: string },
            res: {
              statusCode: number
              setHeader: (name: string, value: string) => void
              end: (body?: string) => void
            },
            next: (error?: unknown) => void
          ) => void
        ) => void
      }
    }) {
      if (!enabled) return
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith('/api/')) {
          next()
          return
        }

        void (async () => {
          const requestUrl = new URL(req.url ?? '/', 'http://localhost')
          const pathname = requestUrl.pathname

          const sendJson = (statusCode: number, payload: unknown) => {
            res.statusCode = statusCode
            res.setHeader('Content-Type', 'application/json; charset=utf-8')
            res.setHeader('Cache-Control', 'no-cache')
            res.end(JSON.stringify(payload))
          }

          if (pathname === '/api/years.json') {
            const files = await readdir(resolve(dataRoot, 'year'))
            const years = files
              .filter((file) => /^\d{4}\.json$/.test(file))
              .map((file) => Number(file.slice(0, 4)))
              .sort((a, b) => b - a)
            sendJson(200, years)
            return
          }

          if (pathname === '/api/typhoons') {
            const year = Number(requestUrl.searchParams.get('year'))
            if (!Number.isInteger(year) || year < 1900 || year > 2200) {
              sendJson(400, { message: 'year must be a four-digit number' })
              return
            }

            const query = requestUrl.searchParams.get('q')?.trim().toLowerCase() ?? ''
            const landingOnly = requestUrl.searchParams.get('landing') === '1'
            const limit = Math.min(
              Math.max(Number(requestUrl.searchParams.get('limit') ?? 60), 1),
              200
            )
            const source = await readFile(resolve(dataRoot, 'year', `${year}.json`), 'utf8')
            const items = JSON.parse(source).filter((item: Record<string, unknown>) => {
              const searchable = `${item.tfbh ?? ''} ${item.name ?? ''} ${item.ename ?? ''}`.toLowerCase()
              const matchesQuery = !query || searchable.includes(query)
              const matchesLanding =
                !landingOnly || (Array.isArray(item.land) && item.land.length > 0)
              return matchesQuery && matchesLanding
            })
            sendJson(200, items.slice(0, limit))
            return
          }

          const typhoonMatch = pathname.match(/^\/api\/(?:typhoons\/|)([A-Za-z0-9_-]+)(?:\.json)?$/)
          if (typhoonMatch) {
            const id = typhoonMatch[1]
            const source = await readFile(resolve(dataRoot, 'typhoon', `${id}.json`), 'utf8')
            const parsed = JSON.parse(source)
            sendJson(200, Array.isArray(parsed) ? parsed[0] ?? null : parsed)
            return
          }

          next()
        })().catch((error: NodeJS.ErrnoException) => {
          if (error.code === 'ENOENT') {
            res.statusCode = 404
            res.setHeader('Content-Type', 'application/json; charset=utf-8')
            res.end(JSON.stringify({ message: 'data not found' }))
            return
          }
          next(error)
        })
      })
    }
  }
}

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyEnabled = env.VITE_HTTP_PROXY !== 'N'
  const apiTarget = env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [
      vue(),
      cesium(),
      localTyphoonDataPlugin(!proxyEnabled),
      AutoImport({
        imports: ['vue', 'vue-router', 'pinia', '@vueuse/core'],
        include: [/\.[tj]sx?$/, /\.vue$/, /\.vue\?vue/, /\.md$/],
        dts: 'src/typings/auto-imports.d.ts'
      }),
      Components({
        dts: 'src/typings/components.d.ts'
      })
    ],
    resolve: {
      alias: {
        '@': resolve(__dirname, 'src')
      }
    },
    server: {
      host: true,
      port: 10060,
      proxy: proxyEnabled
        ? {
          '/health': {
            target: apiTarget,
            changeOrigin: true,
            secure: false
          },
          '/api': {
            target: apiTarget,
            changeOrigin: true,
            secure: false
          }
        }
        : undefined
    },
    css: {
      preprocessorOptions: {
        scss: {
          api: 'modern'
        }
      }
    }
  }
})
