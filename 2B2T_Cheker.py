#IMPORTS
from mcstatus import MinecraftServer
import colorama
import random
#INITIALIZE
colorama.init()
server = MinecraftServer.lookup("2b2t.org")
#INIT Functions
def CheckerClean(a):
	try:
		status=server.status()
		Response = colorama.Fore.BLACK + colorama.Back.GREEN + f"Онлайн: {status.players.online} игроков. Сервер ответил в {status.latency} ms"
	except:
		Response = colorama.Fore.BLACK + colorama.Back.RED + "Ошибка"
	return Response
#Checker
print("2b2t online check by BG")
while True:
	# 'status' is supported by all Minecraft servers that are version 1.7 or higher.
	#Trying to check server
	CheckResponse = CheckerClean(1)
	print(CheckResponse)
	print(colorama.Style.RESET_ALL)