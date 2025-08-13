import discord
from discord.ext import commands, tasks
from collections import defaultdict
import asyncio
import secrets
import json
import os

intents = discord.Intents.all()
intents.members = True

bot = commands.Bot(command_prefix='/', intents=intents)

# Файл для хранения данных бота
DATA_FILE = 'bot_data.json'

# Словарь для хранения отслеживаемых каналов и ролей (храним по ID сервера)
tracked_channels = defaultdict(dict)
update_tasks = {}
bot_is_running = defaultdict(bool)
USER_ACTIVATION_KEYS = {}
BOT_OWNER_ID = 278224933971165184  # Замените на свой ID
MEMBERS_PER_EMBED = 15  # Ограничение в 15 человек на Embed
MAX_NAME_LENGTH = 30  # Максимальная длина имени пользователя для отображения


# Функция для загрузки данных из файла
def load_data():
    try:
        with open(DATA_FILE, 'r') as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    return data


# Функция для сохранения данных в data в файл
def save_data(data):
    with open(DATA_FILE, 'w') as f:
        json.dump(data, f, indent=4)


# Загружаем данные при запуске бота
bot_data = load_data()


async def is_bot_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == BOT_OWNER_ID


def generate_activation_key(length=16):
    return secrets.token_hex(length // 2)


async def sync_commands():
    print("Вызвана функция sync_commands()")  # Проверяем, что функция вызвана
    for guild in bot.guilds:
        try:
            bot.tree.copy_global_to(guild=guild)
            synced = await bot.tree.sync(guild=guild)
            print(f"Synced {len(synced)} application commands to guild {guild.name}")
        except discord.HTTPException as e:
            print(f"Discord HTTP Exception for guild {guild.name}: {e}")
        except discord.Forbidden as e:
            print(f"Discord Forbidden Exception for guild {guild.name}: {e}.  Бот не имеет прав?")
        except Exception as e:
            print(f"General error syncing commands for guild {guild.name}: {e}")


@bot.command()
@commands.is_owner()
async def sync_command(ctx: commands.Context, guild: discord.Guild = None):
    """Синхронизирует команды."""
    if guild is None:
        synced = await bot.tree.sync()
        await ctx.send(f"Синхронизировано {len(synced)} глобальных команд.")
    else:
        bot.tree.copy_global_to(guild=guild)
        synced = await ctx.send(f"Синхронизировано {len(synced)} команд для гильдии {guild.name}.")


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.NotOwner):
        await ctx.send("Только владелец бота может использовать эту команду.")


@bot.event
async def on_guild_join(guild: discord.Guild):
    """Синхронизирует команды при присоединении к новой гильдии."""
    try:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"Synced {len(synced)} application commands to guild {guild.name} after joining.")
    except discord.HTTPException as e:
        print(f"Discord HTTP Exception after joining for guild {guild.name}: {e}")
    except discord.Forbidden as e:
        print(f"Discord Forbidden Exception after joining for guild {guild.name}: {e}. Бот не имеет прав?")
    except Exception as e:
        print(f"Error syncing commands for guild {guild.name} after joining: {e}")


command_sync_lock = asyncio.Lock()  # Создаём блокировку


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name}')
    await bot.wait_until_ready()

    async with command_sync_lock:  # Проверяем блокировку
        await asyncio.sleep(5)  # Задержка в 5 секунд
        print("Вызвана функция sync_commands()")  # Проверяем, что сюда дошло
        await sync_commands()  # Запускаем синхронизацию команд при запуске

    # Инициализируем данные для новых серверов и восстанавливаем состояние
    for guild in bot.guilds:
        guild_id = str(guild.id)
        if guild_id not in bot_data:
            bot_data[guild_id] = {
                'tracked_channels': [],
                'tracked_roles': defaultdict(list),  # Добавляем tracked_roles
                'bot_is_running': False,  # Добавляем bot_is_running
                'last_action': None
            }
        else:
            # Восстанавливаем состояние
            tracked_channels_data = bot_data[guild_id][
                'tracked_channels']  # Переименовал переменную, чтобы не совпадала с tracked_channels
            tracked_channels[int(guild_id)] = {}  # Теперь это безопасно, потому что tracked_channels - словарь.
            for channel_id in tracked_channels_data:
                channel = bot.get_channel(channel_id)
                if channel:
                    print(f'Восстановлено отслеживание канала {channel.name} на сервере {guild.name}')
                    if not tracked_channels[int(guild_id)].get(channel_id):
                        tracked_channels[int(guild_id)][channel_id] = bot_data[guild_id]['tracked_roles'].get(
                            str(channel_id), [])
                else:
                    print(f'Канал с ID {channel_id} не найден на сервере {guild.name}. Удален из списка отслеживаемых.')
                    bot_data[guild_id]['tracked_channels'].remove(channel_id)
            bot_is_running[int(guild_id)] = bot_data[guild_id]['bot_is_running']
            # tracked_channels[int(guild_id)] = defaultdict(list) #  Удаляем эту строку
            for channel_id_str, roles in bot_data[guild_id]['tracked_roles'].items():
                channel_id = int(channel_id_str)
                if not tracked_channels[int(guild_id)].get(channel_id):  # Добавляем проверку
                    tracked_channels[int(guild_id)][channel_id] = []
                for role_id in roles:
                    role = guild.get_role(role_id)
                    if role:
                        tracked_channels[int(guild_id)][channel_id].append(role)
                    else:
                        print(f"Роль с ID {role_id} не найдена на сервере {guild.name}")

    save_data(bot_data)


@bot.tree.command(name="request_key", description="Запрашивает ключ активации у владельца бота.")
async def request_key(interaction: discord.Interaction):
    """Requests an activation key from the bot owner and sends the key via DM."""
    guild_id = str(interaction.guild.id)
    await interaction.response.defer(ephemeral=True)
    generated_key = generate_activation_key()
    USER_ACTIVATION_KEYS[guild_id] = generated_key

    owner = bot.get_user(BOT_OWNER_ID)
    if owner:
        try:
            await owner.send(
                f"Ключ активации для сервера '{interaction.guild.name}' (ID: {guild_id}): `{generated_key}`")
            await interaction.followup.send("Запрос на ключ активации отправлен владельцу бота.", ephemeral=True)
        except discord.errors.Forbidden:
            await interaction.followup.send(
                "Невозможно отправить сообщение владельцу бота. Убедитесь, что у него открыты личные сообщения и что бот может отправлять сообщения.",
                ephemeral=True)
        except Exception as e:
            print(f"Ошибка при отправке DM владельцу: {e}")
            await interaction.followup.send("Произошла ошибка при отправке сообщения владельцу бота.", ephemeral=True)
    else:
        await interaction.followup.send("Не удалось найти владельца бота.", ephemeral=True)


@bot.tree.command(name="activate", description="Активирует бота с помощью ключа активации.")
async def activate(interaction: discord.Interaction, activation_key: str):
    """Activates the bot for a specific server."""
    guild_id = str(interaction.guild.id)

    if bot_is_running[int(guild_id)]:
        await interaction.response.send_message("Бот уже активирован на этом сервере.", ephemeral=True)
        return

    if guild_id not in USER_ACTIVATION_KEYS:
        await interaction.response.send_message("Сначала запросите ключ активации командой /request_key.",
                                                ephemeral=True)
        return

    stored_key = USER_ACTIVATION_KEYS[guild_id]

    if activation_key == stored_key:
        bot_is_running[int(guild_id)] = True
        bot_data[guild_id]['bot_is_running'] = True  # Сохраняем состояние
        save_data(bot_data)
        del USER_ACTIVATION_KEYS[guild_id]
        await interaction.response.send_message("Бот успешно активирован на этом сервере!", ephemeral=True)
    else:
        await interaction.response.send_message("Неверный ключ активации.", ephemeral=True)
        bot_is_running[int(guild_id)] = False
        bot_data[guild_id]['bot_is_running'] = False  # Сохраняем состояние
        save_data(bot_data)


@bot.tree.command(name="mark", description="Помечает канал для отслеживания.")
async def mark(interaction: discord.Interaction):
    """Marks a channel for tracking."""
    guild_id = str(interaction.guild.id)
    channel_id = interaction.channel.id

    if not bot_is_running[int(guild_id)]:
        await interaction.response.send_message("Бот еще не активирован на этом сервере.", ephemeral=True)
        return
    if interaction.channel.id not in tracked_channels[int(guild_id)]:
        tracked_channels[int(guild_id)][interaction.channel.id] = []
    bot_data[guild_id]['tracked_channels'].append(interaction.channel.id)
    bot_data[guild_id]['tracked_roles'][str(interaction.channel.id)] = []  # Создаем пустой список для ролей
    save_data(bot_data)
    embed = discord.Embed(description=f"✅ Канал {interaction.channel.mention} помечен для отслеживания.",
                          color=0x2ECC71)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="add_role", description="Добавляет роль в список отслеживаемых ролей.")
async def add_role(interaction: discord.Interaction, role1: discord.Role, role2: discord.Role = None,
                   role3: discord.Role = None, role4: discord.Role = None, role5: discord.Role = None):
    """Adds up to 5 roles to the tracked roles list.
    role1 is required, role2 through role5 are optional.
    """
    guild_id = str(interaction.guild.id)
    channel_id = interaction.channel.id
    if not bot_is_running[int(guild_id)]:
        await interaction.response.send_message("Бот еще не активирован на этом сервере.", ephemeral=True)
        return

    if interaction.channel.id not in tracked_channels[int(guild_id)]:
        embed = discord.Embed(description="❌ Сначала используйте команду /mark.", color=0xE74C3C)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    added_roles = []
    roles_to_add = [role1, role2, role3, role4, role5]

    if channel_id not in tracked_channels[int(guild_id)]:  # Проверка канала в tracked_channels
        print("Канал не найден!")
        return

    roles = tracked_channels[int(guild_id)][channel_id]
    bot_data[guild_id]['tracked_roles'][str(interaction.channel.id)] = []  # Обновляем список ролей

    for role in roles_to_add:
        if role:
            if role not in roles:  # Проверяем, что роль еще не добавлена
                roles.append(role)
                added_roles.append(role.name)
                bot_data[guild_id]['tracked_roles'][str(interaction.channel.id)].append(role.id)  # Сохраняем ID роли
    save_data(bot_data)

    embed_description = ""
    if added_roles:
        embed_description += f"✅ Добавлены роли: {', '.join(added_roles)}\n"
    else:
        embed_description += "❌ Не удалось добавить роли."

    embed = discord.Embed(description=embed_description, color=0x2ECC71 if added_roles else 0xE74C3C)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="unmark", description="Удаляет канал из отслеживаемых.")
async def unmark(interaction: discord.Interaction):
    """Unmarks a channel for tracking."""
    guild_id = str(interaction.guild.id)
    channel_id = str(interaction.channel.id)
    if not bot_is_running[int(guild_id)]:
        await interaction.response.send_message("Бот еще не активирован на этом сервере.", ephemeral=True)
        return

    if interaction.channel.id in tracked_channels[int(guild_id)]:
        del tracked_channels[int(guild_id)][interaction.channel.id]
        bot_data[guild_id]['tracked_channels'].remove(interaction.channel.id)
        del bot_data[guild_id]['tracked_roles'][channel_id]  # Удаляем инфу о ролях
        save_data(bot_data)
        embed = discord.Embed(description=f"✅ Канал {interaction.channel.mention} удалён из отслеживаемых.",
                              color=0x2ECC71)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = discord.Embed(description="❌ Этот канал не отслеживается.", color=0xE74C3C)
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="list_users", description="Выводит список пользователей с ролями.")
async def list_users(interaction: discord.Interaction):
    """Lists users with roles in separate embed messages with the role name as the title."""
    guild_id = str(interaction.guild.id)
    if not bot_is_running[int(guild_id)]:
        await interaction.response.send_message("Бот еще не активирован на этом сервере.", ephemeral=True)
        return

    # Используем вебхук для отправки сообщения от имени бота
    channel = interaction.channel
    try:
        webhooks = await channel.webhooks()
        webhook = await channel.create_webhook(name="HelperWebhook")
    except discord.errors.NotFound:
        await interaction.response.send_message(
            "Не удалось создать вебхук. Убедитесь, что у бота есть право 'Управление вебхуками'.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=False)  # Сделано не ephemeral

    if interaction.channel.id not in tracked_channels[int(guild_id)]:
        embed = discord.Embed(description="❌ Сначала используйте команду /mark.", color=0xE74C3C)
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    roles = tracked_channels[int(guild_id)][interaction.channel.id]

    # Создаем множество для отслеживания уже учтенных пользователей
    counted_users = set()

    # Сортируем роли по порядку добавления
    def get_role_index(role):
        try:
            return tracked_channels[int(guild_id)][interaction.channel.id].index(role)
        except ValueError:
            return float('inf')

    roles = sorted(roles, key=get_role_index)

    embeds = []  # Список для хранения всех embed сообщений
    total_member_count = 0  # Общее количество пользователей

    for role in roles:
        members = role.members
        if members:
            member_strings = []
            count = 0
            for member in members:
                # Проверяем, был ли пользователь уже учтен
                if member.id not in counted_users:
                    count += 1
                    display_name = (member.display_name[:MAX_NAME_LENGTH] + "...") if len(
                        member.display_name) > MAX_NAME_LENGTH else member.display_name
                    if len(members) == 1:
                        member_strings.append(f"{member.mention} - {display_name}")
                    else:
                        member_strings.append(f"{count}. {member.mention} - {display_name}")
                    # Добавляем пользователя в множество учтенных
                    counted_users.add(member.id)

            # Разбиваем список пользователей на чанки по 15 человек
            chunks = [member_strings[i:i + MEMBERS_PER_EMBED] for i in range(0, len(member_strings), MEMBERS_PER_EMBED)]
            total_embeds = len(chunks)

            for chunk in chunks:
                description = "\n".join(chunk)
                embed_title = f"{role.name:<20}"  # Добавляем пробелы, чтобы заголовок занимал 20 символов

                embed = discord.Embed(title=embed_title, color=role.color)
                embed.description = description  # Устанавливаем описание Embed

                if description:  # Отправляем только если есть описание (есть участники)
                    embeds.append(embed)  # Добавляем в список

    total_member_count = len(counted_users)  # Считаем кол-во пользователей

    # Создаём embed с итоговым количеством участников
    total_embed = discord.Embed(title="Итого", description=f"Общее количество участников: {total_member_count}",
                                color=discord.Color.dark_purple())
    embeds.append(total_embed)  # Добавляем в конец

    # Отправляем Embed сообщения
    for embed in embeds:
        try:
            await webhook.send(embed=embed, username=bot.user.name, avatar_url=bot.user.avatar.url)
        except discord.errors.HTTPException as e:
            print(f"Ошибка при отправке embed: {e}")
            await interaction.followup.send(
                f"Ошибка: Не удалось отправить итоговое сообщение с количеством участников.", ephemeral=True)

    await interaction.delete_original_response()


@bot.tree.command(name='set_update_time', description="Устанавливает таймер для обновления сообщения (в часах).")
async def set_update_time(interaction: discord.Interaction, hours: int):
    """Sets the timer for updating the user list message in hours."""
    guild_id = str(interaction.guild.id)
    if not bot_is_running[int(guild_id)]:
        await interaction.response.send_message("Бот еще не активирован на этом сервере.", ephemeral=True)
        return

    if interaction.channel.id not in tracked_channels[int(guild_id)]:
        embed = discord.Embed(description="❌ Сначала используйте команду /mark.", color=0xE74C3C)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    global update_tasks

    if guild_id in update_tasks and update_tasks[guild_id]:
        update_tasks[guild_id].cancel()

    seconds = hours * 3600

    async def update_loop(interact: discord.Interaction):  # Изменяем аргумент
        try:
            while True:
                try:
                    await list_users(interact)  # Передаём interaction
                except discord.NotFound:
                    print("Interaction not found. The original interaction may have expired.")
                    break  # Выходим из цикла while
                except Exception as e:
                    print(f"Error occurred during the update loop: {e}")
                    break  # Выходим из цикла while
                await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            print("Update loop cancelled.")

    update_tasks[guild_id] = bot.loop.create_task(update_loop(interaction))  # Добавляем аргумент
    embed = discord.Embed(description=f"⏱️ Таймер обновления установлен на {hours} часов.", color=0xF1C40F)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name='stop_update_time', description='Останавливает таймер обновления списка пользователей.')
async def stop_update_time(interaction: discord.Interaction):
    """Stops the timer for updating the user list message."""
    guild_id = str(interaction.guild.id)
    if not bot_is_running[int(guild_id)]:
        await interaction.response.send_message("Бот еще не активирован на этом сервере.", ephemeral=True)
        return

    global update_tasks
    if guild_id in update_tasks and update_tasks[guild_id]:
        update_tasks[guild_id].cancel()
        update_tasks[guild_id] = None
        embed = discord.Embed(description="✅ Таймер обновления остановлен.", color=0x2ECC71)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = discord.Embed(description="❌ Таймер обновления не был запущен.", color=0xE74C3C)
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="remove_role", description="Удаляет роль из списка отслеживаемых ролей.")
async def remove_role(interaction: discord.Interaction, role: discord.Role):
    """Removes a role from the tracked roles list."""
    guild_id = str(interaction.guild.id)
    channel_id = str(interaction.channel.id)
    if not bot_is_running[int(guild_id)]:
        await interaction.response.send_message("Бот еще не активирован на этом сервере.", ephemeral=True)
        return

    if interaction.channel.id not in tracked_channels[int(guild_id)]:
        embed = discord.Embed(description="❌ Сначала используйте команду /mark.", color=0xE74C3C)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    roles = tracked_channels[int(guild_id)][interaction.channel.id]
    if role in roles:
        roles.remove(role)
        # Обновляем bot_data
        bot_data[guild_id]['tracked_roles'][channel_id] = [r.id for r in roles]
        save_data(bot_data)
        embed = discord.Embed(description=f"✅ Роль {role.name} удалена из списка отслеживаемых.", color=0x2ECC71)
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        embed = discord.Embed(description="❌ Эта роль не отслеживается в этом канале.", color=0xE74C3C)
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="list_tracked_roles", description="Показывает список отслеживаемых ролей в текущем канале.")
async def list_tracked_roles(interaction: discord.Interaction):
    """Shows the list of tracked roles in the current channel."""
    guild_id = str(interaction.guild.id)
    if not bot_is_running[int(guild_id)]:
        await interaction.response.send_message("Бот еще не активирован на этом сервере.", ephemeral=True)
        return

    if interaction.channel.id not in tracked_channels[int(guild_id)]:
        embed = discord.Embed(description="❌ Сначала используйте команду /mark.", color=0xE74C3C)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    roles = tracked_channels[int(guild_id)][interaction.channel.id]
    if roles:
        role_names = "\n".join([role.name for role in roles])
        embed = discord.Embed(title="Отслеживаемые роли:", description=role_names, color=0x3498DB)
    else:
        embed = discord.Embed(description="В этом канале не отслеживаются роли.", color=0xE67E22)
        await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="clear_tracked_roles", description="Удаляет все отслеживаемые роли из текущего канала.")
async def clear_tracked_roles(interaction: discord.Interaction):
    """Clears all tracked roles from the current channel."""
    guild_id = str(interaction.guild.id)
    channel_id = str(interaction.channel.id)
    if not bot_is_running[int(guild_id)]:
        await interaction.response.send_message("Бот еще не активирован на этом сервере.", ephemeral=True)
        return

    if interaction.channel.id not in tracked_channels[int(guild_id)]:
        embed = discord.Embed(description="❌ Сначала используйте команду /mark.", color=0xE74C3C)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return

    tracked_channels[int(guild_id)][interaction.channel.id] = []
    bot_data[guild_id]['tracked_roles'][channel_id] = []  # Очищаем список
    save_data(bot_data)
    embed = discord.Embed(description="✅ Список отслеживаемых ролей очищен.", color=0x2ECC71)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="bot_status", description="Показывает статус бота на этом сервере.")
async def bot_status(interaction: discord.Interaction):
    """Shows the bot's status on the server."""
    guild_id = str(interaction.guild.id)
    status = "✅ Активен" if bot_is_running[int(guild_id)] else "❌ Неактивен"
    embed = discord.Embed(title="Статус бота:", description=status,
                          color=0x2ECC71 if bot_is_running[int(guild_id)] else 0xE74C3C)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.event
async def on_message(message):
    """Обработка сообщений."""
    # Проверяем, что сообщение не от бота
    if message.author == bot.user:
        return

    guild_id = str(message.guild.id)
    channel_id = message.channel.id

    # Проверяем, отслеживается ли канал
    if channel_id in bot_data[guild_id]['tracked_channels']:
        print(f'Сообщение в отслеживаемом канале: {message.content}')
        bot_data[guild_id]['last_action'] = f'Сообщение в канале {message.channel.name}: {message.content[:50]}...'
        save_data(bot_data)

    await bot.process_commands(message)  # Необходимо для обработки команд ыв

bot.run('MTM3Njk0NzY5MDIwMTY3Nzk3NQ.G27fcf.odNkZ56hxlfX0WEhc325wDgXHuKYR9XzIeLE8o')