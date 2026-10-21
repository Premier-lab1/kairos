# Kairos: развёртывание на сервере Hetzner CAX21

Пошаговая инструкция для владельца проекта. Команды копируйте целиком и выполняйте по порядку. Если шаг кончился ошибкой, не идите дальше: скопируйте текст ошибки и покажите разработчику.

Обозначения, чтобы не путаться, где выполнять команду:

- **[Windows PowerShell]** — программа PowerShell на вашем ПК.
- **[Git Bash]** — программа Git Bash на вашем ПК.
- **[Сервер]** — окно, где вы вошли на сервер по `ssh`.

В командах замените `IP_СЕРВЕРА` на адрес вашего сервера (его покажет Hetzner, вид `203.0.113.10`).

Что получится в итоге: на сервере в Docker работают база PostgreSQL с TimescaleDB, Redis и сам бэкенд Kairos. Наружу открыт только SSH (порт 22); база и Redis из интернета недоступны.

---

## 1. Ключ SSH на Windows

SSH-ключ — это «замок и ключ» для входа на сервер без пароля. Делается один раз.

**[Windows PowerShell]**

```powershell
ssh-keygen -t ed25519 -C "kairos"
```

На вопросы «Enter file…» и «Enter passphrase» можно просто нажать Enter (парольная фраза усилит защиту, но тогда её придётся вводить при каждом входе). Ключ сохранится в папку `C:\Users\ВАШ_ПОЛЬЗОВАТЕЛЬ\.ssh\`: файл `id_ed25519` — секретный, никому его не отправляйте; `id_ed25519.pub` — публичный, его мы отдадим Hetzner.

Показать публичный ключ (одна длинная строка, начинается с `ssh-ed25519`):

**[Windows PowerShell]**

```powershell
Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub
```

Скопируйте эту строку целиком.

Если команда `ssh-keygen` не найдена: Параметры Windows → Приложения → Дополнительные компоненты → добавить «Клиент OpenSSH». Либо выполните ту же команду в Git Bash.

## 2. Создание сервера в Hetzner Cloud

1. Откройте <https://console.hetzner.cloud>, войдите, создайте проект (например, `kairos`).
2. В проекте: **Security → SSH keys → Add SSH key**. Вставьте строку из шага 1, имя — любое (например, `home-pc`).
3. **Servers → Add Server** и выберите:
   - **Location:** Falkenstein, Nuremberg или Helsinki (в них доступна ARM-серия CAX).
   - **Image:** Ubuntu 24.04.
   - **Type:** вкладка **Shared vCPU → Arm64 (Ampere)** → **CAX21** (4 vCPU, 8 ГБ RAM, 80 ГБ диск).
   - **Networking:** оставьте публичный IPv4 (и IPv6, если включён по умолчанию).
   - **SSH keys:** отметьте ключ из пункта 2.
   - **Backups:** по желанию. Это автоматические снимки всего сервера, платная опция (около 20 % от цены сервера; точную цену смотрите в консоли). Хорошая страховка, но не замена дампам базы из раздела 11.
   - **Name:** `kairos`.
4. Нажмите **Create & Buy now**. Через минуту сервер получит IP-адрес — запишите его.

## 3. Первый вход и новый пользователь

Работать под `root` постоянно небезопасно. Создадим обычного пользователя `kairos` с правом `sudo`.

**[Windows PowerShell]** — первый вход:

```powershell
ssh root@IP_СЕРВЕРА
```

На вопрос «Are you sure you want to continue connecting» ответьте `yes`. Дальше вы на сервере.

**[Сервер]** (под root) — обновление системы:

```bash
apt update && apt upgrade -y
```

**[Сервер]** (под root) — создание пользователя. Команда спросит пароль для нового пользователя (придумайте и сохраните: он нужен для `sudo`), остальные вопросы можно пропустить Enter'ом:

```bash
adduser kairos
usermod -aG sudo kairos
mkdir -p /home/kairos/.ssh
cp /root/.ssh/authorized_keys /home/kairos/.ssh/authorized_keys
chown -R kairos:kairos /home/kairos/.ssh
chmod 700 /home/kairos/.ssh
chmod 600 /home/kairos/.ssh/authorized_keys
```

**Не закрывая это окно**, откройте второе окно PowerShell и проверьте, что вход под новым пользователем работает:

**[Windows PowerShell, второе окно]**

```powershell
ssh kairos@IP_СЕРВЕРА
sudo whoami
```

Должно напечататься `root` (после ввода пароля пользователя `kairos`). Если не получилось, не переходите дальше: исправляйте в первом окне.

## 4. Отключение входа по паролю и под root

Дальше работаем в окне, где вы вошли как `kairos`.

**[Сервер]**

```bash
sudo tee /etc/ssh/sshd_config.d/01-kairos-hardening.conf <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sudo sshd -t && sudo systemctl restart ssh
```

Файл с именем `01-...` читается раньше системных, поэтому его настройки главные. Проверка:

**[Сервер]**

```bash
sudo sshd -T | grep -Ei '^(passwordauthentication|permitrootlogin|kbdinteractiveauthentication)'
```

Должно быть: `passwordauthentication no`, `permitrootlogin no`, `kbdinteractiveauthentication no`.

Теперь **не закрывая** текущее окно откройте ещё одно и убедитесь, что вход по ключу всё ещё работает (`ssh kairos@IP_СЕРВЕРА`). Только после этого закрывайте старые окна.

## 5. Файрвол ufw (пока только SSH)

**[Сервер]**

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw --force enable
sudo ufw status verbose
```

В списке должен быть `22/tcp ALLOW IN`. Порты 80 и 443 откроем позже, когда появится веб-приложение и Caddy.

Важно: порты, которые Docker публикует через `ports:`, ufw НЕ закрывает (Docker обходит его правила). В нашем `docker-compose.yml` нет ни одного `ports:`, поэтому база и Redis снаружи недоступны. Не добавляйте `ports:` для `db` и `redis`.

## 6. Автоматические обновления безопасности

**[Сервер]**

```bash
sudo apt install -y unattended-upgrades
sudo tee /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
sudo systemctl status unattended-upgrades --no-pager | head -5
```

Должно быть `active`. Автоматической перезагрузки мы не включаем; изредка (раз в месяц-два) заходите и, если есть файл `/var/run/reboot-required`, выполняйте `sudo reboot` (контейнеры сами поднимутся после перезагрузки).

## 7. Установка Docker Engine и compose

Команды взяты из официальной инструкции Docker для Ubuntu, раздел «Install using the apt repository»: <https://docs.docker.com/engine/install/ubuntu/> (Ubuntu 24.04 «Noble» в списке поддерживаемых). Если Docker когда-нибудь изменит инструкцию, ориентируйтесь на страницу.

**[Сервер]** — подключение репозитория Docker:

```bash
sudo apt update
sudo apt install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

sudo tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt update
```

**[Сервер]** — установка:

```bash
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo docker run --rm hello-world
```

Должно появиться «Hello from Docker!». Затем разрешим пользователю `kairos` работать с Docker без `sudo` (инструкция: <https://docs.docker.com/engine/install/linux-postinstall/>). Предупреждение Docker: членство в группе `docker` равносильно правам root, поэтому в эту группу добавляем только `kairos`.

**[Сервер]**

```bash
sudo usermod -aG docker kairos
```

Теперь выйдите и зайдите снова, чтобы группа применилась:

**[Сервер]**

```bash
exit
```

**[Windows PowerShell]**

```powershell
ssh kairos@IP_СЕРВЕРА
```

**[Сервер]** — проверка (без `sudo`):

```bash
docker version --format '{{.Server.Version}}'
docker compose version
```

Docker на Ubuntu запускается при загрузке сервера сам.

## 8. Копирование проекта с Windows-ПК на сервер

Копируем через `tar` по SSH: он есть в Git Bash, понимает исключения и не требует установки `rsync`. Не копируются: `.venv`, `.localdb` (локальная база), видео `*.MP4`, `node_modules`, кэши, служебная папка `.astra`, фото-материалы и **файл `.env`** (его мы создадим на сервере сами, потому что локальный указывает на базу на вашем ПК).

**[Git Bash]**

```bash
cd /x/Kairos
tar czf - \
  --exclude='.venv' --exclude='.localdb' --exclude='*.MP4' --exclude='node_modules' \
  --exclude='.env' --exclude='.env.*' --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.astra' \
  --exclude='photo_*.jpg' --exclude='.git' \
  . | ssh kairos@IP_СЕРВЕРА "mkdir -p ~/kairos && tar xzf - -C ~/kairos --no-same-owner"
```

Сообщение «file changed as we read it» можно игнорировать. Проверьте, что файлы приехали:

**[Сервер]**

```bash
ls ~/kairos ~/kairos/deploy ~/kairos/backend
```

Должны быть `backend`, `deploy`, `docs` и т. д.; в `deploy` — `docker-compose.yml`, `check-arm-stack.sh`, `README.md`; в `backend` — `Dockerfile`, `pyproject.toml`, `kairos`, `migrations`.

## 9. Файл `.env` на сервере и первый запуск

Файл `.env` лежит в корне проекта (`~/kairos/.env`) и содержит секреты. В нём нужны две переменные:

- `POSTGRES_PASSWORD` — пароль базы (при первом запуске он «запоминается» в базе; менять его в файле потом недостаточно, и терять его не стоит);
- `FRONTRUN_API_KEY` — ключ Frontrun (то же значение, что в вашем локальном `X:\Kairos\.env`).

Строку `DATABASE_URL` в серверный `.env` добавлять **не нужно**: Docker Compose сам подставит адрес базы внутри сети (`db:5432`) и пароль. Даже если вы добавите свою строку, в контейнере она будет перекрыта.

**[Сервер]** — создание файла со случайным паролем:

```bash
cd ~/kairos
cat > .env <<EOF
POSTGRES_PASSWORD=$(openssl rand -hex 24)
FRONTRUN_API_KEY=ВСТАВЬТЕ_КЛЮЧ_СЮДА
EOF
chmod 600 .env
nano .env
```

В редакторе `nano` замените `ВСТАВЬТЕ_КЛЮЧ_СЮДА` на настоящий ключ (без кавычек и пробелов), сохраните: `Ctrl+O`, Enter, выход: `Ctrl+X`.

Compose берёт `POSTGRES_PASSWORD` из файла `.env`, который лежит рядом с `docker-compose.yml`. Чтобы не хранить два файла, сделайте ссылку (один раз):

**[Сервер]**

```bash
ln -s ../.env ~/kairos/deploy/.env
```

Первый запуск (сборка образа бэкенда занимает несколько минут):

**[Сервер]**

```bash
cd ~/kairos/deploy
docker compose up -d --build
```

Все дальнейшие команды `docker compose ...` выполняйте из каталога `~/kairos/deploy` (иначе Compose не найдёт файл и пароль).

### Проверка

**[Сервер]**

```bash
cd ~/kairos/deploy
docker compose ps
```

Сервисы `db` и `redis` должны быть в состоянии `running (healthy)`, `backend` — `running`.

Логи бэкенда (выход из просмотра: `Ctrl+C`; контейнеры продолжат работать):

**[Сервер]**

```bash
docker compose logs -f --tail 100 backend
```

В начале должна быть строка вида `applied: 0001_init.sql, ...` (при следующих запусках `applied: nothing new`) — это применились миграции базы. Если после этого видите `No module named kairos.pipeline.run`, значит модуль конвейера ещё не написан или не попал на сервер; контейнер будет перезапускаться по кругу, это не страшно, после копирования свежего кода (раздел 12) всё заработает.

Логи базы и Redis:

**[Сервер]**

```bash
docker compose logs --tail 50 db
docker compose logs --tail 50 redis
```

Проверка, что база и TimescaleDB на месте:

**[Сервер]**

```bash
docker compose exec db psql -U kairos -d kairos -c "select name from schema_migrations order by name;"
docker compose exec db psql -U kairos -d kairos -c "select hypertable_name from timescaledb_information.hypertables;"
```

Во втором запросе должны быть таблицы `trades` и `token_snapshots` (если TimescaleDB подхватился; иначе миграция оставляет обычные таблицы).

Остановка и повторный запуск (данные сохраняются в томах Docker):

**[Сервер]**

```bash
docker compose stop
docker compose start
```

Не выполняйте `docker compose down -v` — флаг `-v` удаляет тома, то есть **всю базу**.

## 10. Проверка ARM-совместимости библиотек

Скрипт в чистом контейнере `python:3.12-slim` для `linux/arm64` ставит по очереди ключевые библиотеки (только готовые сборки, без компиляции), пробует их импортировать и печатает таблицу OK/FAIL. Занимает несколько минут.

**[Сервер]**

```bash
cd ~/kairos
bash deploy/check-arm-stack.sh
```

Если в конце «ИТОГ: все библиотеки установились и импортируются» — всё в порядке. Если есть `FAIL`, скопируйте всю таблицу и «Хвост ошибок» и покажите разработчику. (Для `lightgbm` и `xgboost` возможна подсказка про системный пакет `libgomp1`: он в образ пока не добавлен, понадобится, когда эти библиотеки начнут использоваться.)

## 11. Резервные копии Postgres

Дамп всей базы в файл с датой (формат `-Fc` сжатый, подходит для `pg_restore`):

**[Сервер]**

```bash
mkdir -p ~/backups
cd ~/kairos/deploy
docker compose exec -T db pg_dump -U kairos -Fc kairos > ~/backups/kairos-$(date +%F).dump
ls -lh ~/backups
```

Ежедневно в 03:30 UTC копию делает скрипт `deploy/backup.sh` (хранит 14 дней, пишет журнал в `~/backups/backup.log`). Установка в cron одной командой:

**[Сервер]**

```bash
chmod +x ~/kairos/deploy/backup.sh
printf "30 3 * * * /home/kairos/kairos/deploy/backup.sh >> /home/kairos/backups/backup.log 2>&1
" | crontab -
crontab -l
```

Предупреждение `pg_dump` про `circular foreign-key constraints ... continuous_agg` — штатное для TimescaleDB, скрипт его скрывает.

Копии лежат на том же сервере, поэтому при потере сервера пропадут вместе с ним. Время от времени скачивайте их на свой ПК:

**[Git Bash]**

```bash
mkdir -p /x/Kairos-backups
scp "kairos@IP_СЕРВЕРА:backups/*.dump" /x/Kairos-backups/
```

### Восстановление из дампа (только если что-то сломалось)

Это замена всей базы данными из файла. Подставьте имя нужного файла.

**[Сервер]**

```bash
cd ~/kairos/deploy
docker compose stop backend
docker compose exec -T db psql -U kairos -d postgres -c "DROP DATABASE kairos WITH (FORCE);" -c "CREATE DATABASE kairos;"
docker compose exec -T db psql -U kairos -d kairos -c "CREATE EXTENSION IF NOT EXISTS timescaledb;" -c "SELECT timescaledb_pre_restore();"
docker compose exec -T db pg_restore -U kairos -d kairos --no-owner < ~/backups/kairos-ДАТА.dump
docker compose exec -T db psql -U kairos -d kairos -c "SELECT timescaledb_post_restore();" -c "ANALYZE;"
docker compose start backend
```

Порядок шагов (`timescaledb_pre_restore` до восстановления, `timescaledb_post_restore` после) взят из руководства Timescale: <https://www.tigerdata.com/docs/self-hosted/latest/migration/entire-database> (старый адрес docs.timescale.com перенаправляет туда). На реальном сервере эта процедура нами не проверялась: прежде чем на неё рассчитывать, один раз отрепетируйте восстановление на тестовой базе вместе с разработчиком.

Redis бэкапить не нужно: это очередь и кэш.

## 12. Как обновлять после изменений кода

1. Снова скопируйте проект (та же команда, что в разделе 8; `.env` на сервере не затрагивается):

   **[Git Bash]**

   ```bash
   cd /x/Kairos
   tar czf - \
     --exclude='.venv' --exclude='.localdb' --exclude='*.MP4' --exclude='node_modules' \
     --exclude='.env' --exclude='__pycache__' --exclude='.pytest_cache' --exclude='.astra' \
     --exclude='photo_*.jpg' --exclude='.git' --exclude='.next' \
     . | ssh kairos@IP_СЕРВЕРА "mkdir -p ~/kairos && tar xzf - -C ~/kairos --no-same-owner"
   ```

   Заметьте: так на сервер попадают новые и изменённые файлы, но удалённые у вас файлы на сервере остаются. Разработчик скажет, если нужно что-то удалить вручную.

2. Пересоберите и перезапустите (миграции применятся сами при старте бэкенда):

   **[Сервер]**

   ```bash
   cd ~/kairos/deploy
   docker compose up -d --build
   docker compose logs --tail 50 backend
   ```

3. Изредка очищайте старые образы:

   **[Сервер]**

   ```bash
   docker image prune -f
   ```

Если изменили только `.env` (например, новый ключ): `docker compose up -d` пересоздаст контейнеры с новыми значениями. Команда `docker compose restart` новые значения из `.env` **не подхватывает**.

## 13. Сайт и домен xkairos.io

С 18.10.2026 в compose два новых сервиса: `web` (лендинг из `web/`, Next.js standalone, порт 3000 только внутри сети compose) и `caddy` (HTTPS, сертификаты Let's Encrypt получает сам, настройки в `deploy/Caddyfile`). В ufw открыты 80/tcp, 443/tcp и 443/udp.

DNS (Cloudflare, домен куплен на Porkbun):

| Тип | Имя | Значение | Proxy |
| --- | --- | --- | --- |
| A | `@` (xkairos.io) | IP_СЕРВЕРА | DNS only, пока Caddy не получил сертификат |
| CNAME | `www` | xkairos.io | так же |

Когда сайт открылся по `https://xkairos.io`, можно включить Proxy (оранжевое облако) и в SSL/TLS выбрать режим **Full (strict)**. Режим Flexible не выбирать: будет бесконечный редирект.

Обновить только сайт:

**[Сервер]**

```bash
cd ~/kairos/deploy
docker compose up -d --build web
```

Дашборд `https://xkairos.io/app` (с 19.10.2026; с 21.10.2026 открыт без входа, `DASHBOARD_USERS`/`DASHBOARD_SECRET` больше не используются). В базу дашборд ходит под ролью `kairos_ro` (только SELECT, read-only транзакции; пароль `KAIROS_RO_PASSWORD` в `.env`).

Если сертификат не выдаётся: `docker compose logs caddy --tail 30`. После исправления DNS ускорить повторную попытку можно командой `docker compose restart caddy`.

---

## Что изменится при переезде

Что есть сейчас на ПК с Windows и не переносится на сервер «как есть»:

| Сейчас (Windows) | На сервере |
| --- | --- |
| Отдельный кластер PostgreSQL 18 в `X:\Kairos\.localdb\pg`, порт 5434, вход без пароля с localhost | Postgres 17 + TimescaleDB в контейнере `db`, порт наружу не открыт, пароль из `POSTGRES_PASSWORD`, данные в томе Docker `kairos_pgdata` |
| `DATABASE_URL=postgresql://kairos@localhost:5434/kairos` (значение по умолчанию в `kairos/config.py` и в `.env.example`) | `postgresql://kairos:ПАРОЛЬ@db:5432/kairos`; задаётся в `deploy/docker-compose.yml`, в серверный `.env` писать не нужно |
| Данные локальной базы (в т. ч. `kairos_test`) | Не переносятся. Версии Postgres разные (18 локально, 17 на сервере), а данные пока тестовые, поэтому на сервере база начинается с чистой схемы из миграций |
| Локально, вероятно, обычные таблицы без TimescaleDB | На сервере `trades` и `token_snapshots` становятся гипертаблицами (миграция 0001 делает это сама, если расширение доступно) |
| Запуск Postgres командой `pg_ctl` из `X:/Pilgrim Copilot/Postgres/bin` | Не нужен: базой управляет Docker (`docker compose up/stop`) |
| Окружение `backend\.venv`, команды `.venv/Scripts/python`, `.venv/Scripts/pip` | Внутри контейнера Python 3.12 уже настроен. Команды вида `python -m kairos.db` выполняются так: `cd ~/kairos/deploy && docker compose run --rm backend python -m kairos.db`, а например сборщик PumpPortal: `docker compose run --rm backend python -m kairos.collectors.pumpportal --duration 600` |
| Тесты `pytest` (нужна база `kairos_test`) | В образ тесты и dev-зависимости не входят, а база с хоста недоступна. Как запускать тесты на сервере, надо решить отдельно (например, отдельная команда `docker compose run` с dev-зависимостями); пока разумно гонять тесты на ПК |
| Пути `X:\...`, `/x/Kairos`, права и переводы строк Windows | На сервере проект лежит в `/home/kairos/kairos`, в контейнере код в `/app` (`.env` читается из переменных окружения, которые передаёт Compose, а не с диска) |
| Конвейер работает, пока включён ПК | Сервер работает круглосуточно (`restart: unless-stopped`): ежедневный лимит кредитов Frontrun (`FRONTRUN_DAILY_CREDITS`) и платный API начнут расходоваться непрерывно |
| Redis не используется | Контейнер `redis` уже поднимается (с хранением на диск), но код пока к нему не подключён |

## Источники

- Установка Docker Engine на Ubuntu: <https://docs.docker.com/engine/install/ubuntu/>
- Docker без sudo и автозапуск: <https://docs.docker.com/engine/install/linux-postinstall/>
- Восстановление базы TimescaleDB из дампа: <https://www.tigerdata.com/docs/self-hosted/latest/migration/entire-database>
- Выбор стека и сервера: `Kairos.md`, разделы «Решения: стек, сервер, бюджет» и «ARM-совместимость стека».
