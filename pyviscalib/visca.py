#! /usr/bin/env python
# -*- coding: utf8 -*-
#
#	 PyVisca - Implementation of the Visca serial protocol in python
#	 Copyright (C) 2013	 Florian Streibelt pyvisca@f-streibelt.de
#
#	 This program is free software; you can redistribute it and/or modify
#	 it under the terms of the GNU General Public License as published by
#	 the Free Software Foundation, version 2 only.
#
#	 This program is distributed in the hope that it will be useful,
#	 but WITHOUT ANY WARRANTY; without even the implied warranty of
#	 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#	 GNU General Public License for more details.
#
#	 You should have received a copy of the GNU General Public License
#	 along with this program; if not, write to the Free Software
#	 Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA	02110-1301
#	 USA

"""PyVisca by Florian Streibelt <pyvisca@f-streibelt.de>"""

import serial,sys
import time
from thread import allocate_lock

class ViscaError(RuntimeError):
	pass

class ViscaNetworkChange(RuntimeError):
	pass
	
class Visca():
	DEBUG=False

	def __init__(self,portname="/dev/ttyUSB0"):
		self.serialport=None
		self.mutex = allocate_lock()
		self.portname=portname
		self.open_port()
		self.socket_in_use = {}
		self.socket_completed = {}

	def open_port(self):

		with self.mutex:

			if (self.serialport == None):
				try:
					self.serialport = serial.Serial(self.portname,9600,timeout=2,stopbits=1,bytesize=8,rtscts=False, dsrdtr=False)
					self.serialport.flushInput()
				except Exception as e:
					print ("Exception opening serial port '%s' for display: %s\n" % (self.portname,e))
					raise e
					self.serialport = None

	def dump(self,packet,title=None):
		if not self.DEBUG: return
		if not packet or len(packet)==0:
			return

		header=ord(packet[0])
		term=ord(packet[-1:])
		qq=ord(packet[1])

		sender = (header&0b01110000)>>4
		broadcast = (header&0b1000)>>3
		recipient = (header&0b0111)

		if broadcast:
			recipient_s="*"
		else:
			recipient_s=str(recipient)

		print "-----"

		if title:
			print "packet (%s) [%d => %s] len=%d: %s" % (title,sender,recipient_s,len(packet),packet.encode('hex'))
		else:
			print "packet [%d => %s] len=%d: %s" % (sender,sender,recipient_s,len(packet),packet.encode('hex'))

		print " QQ.........: %02x" % qq

		if qq==0x01:
			print "				 (Command)"
		if qq==0x09:
			print "				 (Inquiry)"

		if len(packet)>3:
			rr=ord(packet[2])
			print " RR.........: %02x" % rr

			if rr==0x00:
				print "				 (Interface)"
			if rr==0x04:
				print "				 (Camera [1])"
			if rr==0x06:
				print "				 (Pan/Tilter)"

		if len(packet)>4:
			data=packet[3:-1]
			print " Data.......: %s" % data.encode('hex')
		else:
			print " Data.......: None"

		if not term==0xff:
			print "ERROR: Packet not terminated correctly"
			return

		if len(packet)==3 and ((qq & 0b11110000)>>4)==4:
			socketno = (qq & 0b1111)
			print " packet: ACK for socket %02x" % socketno

		if len(packet)==3 and ((qq & 0b11110000)>>4)==5:
			socketno = (qq & 0b1111)
			print " packet: COMPLETION for socket %02x" % socketno

		if len(packet)>3 and ((qq & 0b11110000)>>4)==5:
			socketno = (qq & 0b1111)
			ret=packet[2:-1].encode('hex')
			print " packet: COMPLETION for socket %02x, data=%s" % (socketno,ret)

		if len(packet)==4 and ((qq & 0b11110000)>>4)==6:
			print " packet: ERROR!"

			socketno = (qq & 0b00001111)
			errcode	 = ord(packet[2])

			#these two are special, socket is zero and has no meaning:
			if errcode==0x02 and socketno==0:
				print "		   : Syntax Error"
			if errcode==0x03 and socketno==0:
				print "		   : Command Buffer Full"


			if errcode==0x04:
				print "		   : Socket %i: Command canceled" % socketno

			if errcode==0x05:
				print "		   : Socket %i: Invalid socket selected" % socketno

			if errcode==0x41:
				print "		   : Socket %i: Command not executable" % socketno

		if len(packet)==3 and qq==0x38:
			print "Network Change - we should immedeately issue a renumbering!"

	def parse_reply_packet(self, packet):
		if not packet or len(packet) < 2:
			return

		header=ord(packet[0])
		term=ord(packet[-1:])
		qq=ord(packet[1])

		sender = (header&0b01110000)>>4
		broadcast = (header&0b1000)>>3
		recipient = (header&0b0111)

		if not term==0xff:
			raise ViscaError("Packet not terminated correctly")

		if len(packet)==3 and qq==0x38:
			raise ViscaNetworkChange("Network Change - we should immedeately issue a renumbering!")

		socketno = qq & 0b00001111
		messagetype = (qq & 0b11110000)>>4

		if len(packet)==4 and messagetype==6:
			errcode	 = ord(packet[2])

			message = "Code=0x%02x, socketno=%d, raw=%s" % (errcode, socketno, packet.encode('hex'))

			#these two are special, socket is zero and has no meaning:
			if errcode==0x02 and socketno==0:
				message = "Syntax error"
			if errcode==0x03 and socketno==0:
				message = "Command buffer full"
			if errcode==0x04:
				message = "Command canceled on socket %d" % socketno
			if errcode==0x05:
				message = "Invalid socket %d" % socketno
			if errcode==0x41:
				message = "Command not currently executable on socket %d" % socketno
			raise ViscaError("Received visca error: %s" % message)

		# It is a valid reply. Check whether it's an ack/completion and update
		# the statuses
		self.adjust_socket_status(sender, socketno, messagetype)
		return packet

	def adjust_socket_status(self, sender, socketno, messagetype):
		if socketno == 0:
			# Inquiry reply, probably
			return
		if messagetype == 4:
			# ACK. Mark the socket as in-use (awaiting a completion).
			if (sender, socketno) in self.socket_in_use:
				raise ViscaError("Received ACK for in-use socket, cam=%d socket=%d" % (sender, socket))
			self.socket_in_use[(sender, socketno)] = True
			if (sender, socketno) in self.socket_completed:
				print 'Warning: received ACK for cam=%d socket=%d for which we had an earlier outstanding command' % (sender, socket)
				del socket_completed[(sender, socketno)]
			return
		if messagetype == 5:
			# Completion. Mark socket as done.
			if not (sender, socketno) in self.socket_in_use:
				print 'Warning: received completion for cam=%d socket=%d for which we had no ACK' % (sender, socket)
				return
			del self.socket_in_use[(sender, socketno)]
			self.socket_completed[(sender, socketno)] = True
			
	def wait_for_cmd_completion(self, packet, timeout=-1):
		if not packet or len(packet) != 3:
			raise ViscaError('wait_for_cmd_completion expects an ACK packet argument')

		header=ord(packet[0])
		qq=ord(packet[1])

		sender = (header&0b01110000)>>4
		socketno = qq & 0b00001111
		messagetype = (qq & 0b11110000)>>4

		if messagetype != 4:
			raise ViscaError('wait_for_cmd_completion expects an ACK packet argument')

		now = time.time()
		while not (sender, socketno) in self.socket_completed:
			if timeout > 0 and time.time() > now + timeout:
				raise ViscaError("Timeout waiting for command completion")
			self.recv_packet()
		del self.socket_completed[(sender, socketno)]
		
	def recv_packet(self,extra_title=None):
		# read up to 16 bytes until 0xff
		packet=''
		count=0
		while count<16:
			s=self.serialport.read(1)
			if s:
				byte = ord(s)
				count+=1
				packet=packet+chr(byte)
			else:
				if count:
					print "ERROR: Timeout waiting for complete reply"
					break
				return None
			if byte==0xff:
				break

		if extra_title:
			self.dump(packet,"recv: %s" % extra_title)
		else:
			self.dump(packet,"recv")
		return self.parse_reply_packet(packet)


	def _write_packet(self,packet):

		if not self.serialport.isOpen():
			raise ViscaError('Serial port is not open')

		# lets see if a completion message or someting
		# else waits in the buffer. If yes dump it.
		if self.serialport.inWaiting():
			if 1 or self.DEBUG:
				print 'visca._write_packet: reading old packet...'
			self.recv_packet("ignored")

		self.serialport.write(packet)
		self.dump(packet,"sent")



	def send_packet(self,recipient,data):
		"""
		according to the documentation:

		|------packet (3-16 bytes)---------|

		 header		message		 terminator
		 (1 byte)  (1-14 bytes)	 (1 byte)

		| X | X . . . . .  . . . . . X | X |

		header:					 terminator:
		1 s2 s1 s0 0 r2 r1 r0	  0xff

		with r,s = recipient, sender msb first

		for broadcast the header is 0x88!

		we use -1 as recipient to send a broadcast!

		"""

		# we are the controller with id=0
		sender = 0

		if recipient==-1:
			#broadcast:
			rbits=0x8
		else:
			# the recipient (address = 3 bits)
			rbits=recipient & 0b111

		sbits=(sender & 0b111)<<4

		header=0b10000000 | sbits | rbits

		terminator=0xff

		packet = chr(header)+data+chr(terminator)

		with self.mutex:

			self._write_packet(packet)

			reply = self.recv_packet()

		return reply

	def send_broadcast(self,data):
		# shortcut
		return self.send_packet(-1,data)



	def i2v(self,value):
		"""
		return word as dword in visca format
		packets are not allowed to be 0xff
		so for numbers the first nibble is 0000
		and 0xfd gets encoded into 0x0f 0x0xd
		"""
		ms = (value &  0b1111111100000000) >> 8
		ls = (value &  0b0000000011111111)
		p=(ms&0b11110000)>>4
		r=(ls&0b11110000)>>4
		q=ms&0b1111
		s=ls&0b1111
		return chr(p)+chr(q)+chr(r)+chr(s)

	def v2i(self, value):
		"""Return dword in visca format is integer"""
		if len(value) != 4:
			raise ViscaError("Invalid visca integer encoding")
		p = ord(value[0])
		q = ord(value[1])
		r = ord(value[2])
		s = ord(value[3])
		if (p&0xf0) or (q&0xf0) or (r&0xf0) or (s&0xf0):
			raise ViscaError("Invalid visca integer encoding")
		return (
			((p & 0x0f) << 12) |
			((q & 0x0f) <<	8) |
			((r & 0x0f) <<	4) |
			((s & 0x0f)		 ))

	def v2i_signed(self, value):
		value = self.v2i(value)
		if value & 0x8000:
			value ^= 0xffff
			value -= 1
		return value
		
	def cmd_adress_set(self):
		"""
		starts enumerating devices, sends the first adress to use on the bus
		reply is the same packet with the next free adress to use
		"""

		#address of first device. should be 1:
		first=1

		reply = self.send_broadcast('\x30'+chr(first)) # set address

		if not reply:
			raise ViscaError("No reply from the bus.")

		if len(reply)!=4 or reply[-1:]!='\xff':
			raise ViscaError("ERROR enumerating devices")
		if reply[0] != '\x88':
			raise ViscaError("Expected broadcast answer to an enumeration request")
		address = ord(reply[2])

		d=address-first
		print "debug: found %i devices on the bus" % d

		if d==0:
			raise ViscaError("No devices on the bus")


	def cmd_if_clear_all(self):
		reply=self.send_broadcast( '\x01\x00\x01') # interface clear all
		if not reply:
			raise ViscaError("No reply to broadcast message")
		if not reply[1:]=='\x01\x00\x01\xff':
			raise ViscaError("ERROR clearing all interfaces on the bus!")

		print "debug: all interfaces clear"


	def cmd_cam(self,device,subcmd):
		packet='\x01\x04'+subcmd
		reply = self.send_packet(device,packet)
		#FIXME: check returned data here and retransmit?

		return reply

	def cmd_pt(self,device,subcmd):
		packet='\x01\x06'+subcmd
		reply = self.send_packet(device,packet)
		#FIXME: check returned data here and retransmit?

		return reply

	def inq_if(self,device,subcmd):
		packet='\x09\x00'+subcmd
		reply = self.send_packet(device,packet)
		#FIXME: check returned data here and retransmit?

		return reply

	def inq_cam(self,device,subcmd):
		packet='\x09\x04'+subcmd
		reply = self.send_packet(device,packet)
		#FIXME: check returned data here and retransmit?

		return reply

	def inq_pt(self,device,subcmd):
		packet='\x09\x06'+subcmd
		reply = self.send_packet(device,packet)
		#FIXME: check returned data here and retransmit?

		return reply

	# Inquiry commands
	def inq_cam_power(self, device):
		reply = self.inq_cam(device, '\x00')
		return ord(reply[2])
		
	def inq_cam_zoom_pos(self, device):
		reply = self.inq_cam(device, '\x47')
		return self.v2i(reply[2:-1])

	def inq_cam_version(self, device):
		reply = self.inq_if(device, '\x02')
		return reply[2:-1]
		
	def inq_cam_id(self, device):
		reply = self.inq_cam(device, '\x22')
		return self.v2i(reply[2:-1])
		
	def inq_cam_videosystem(self, device):
		reply = self.inq_pt(device, '\x23')
		return ord(reply[2])
		
	def inq_cam_pan_tilt_pos(self, device):
		reply = self.inq_pt(device, '\x12')
		pan = self.v2i_signed(reply[2:6])
		tilt = self.v2i_signed(reply[6:-1])
		return pan, tilt
		
	def decode_power(self, rv):
		if rv == 2: return 'on'
		if rv == 3: return 'off'
		if rv == 4: return 'error'
		return 'unknown (0x%x)' % rv
		
	VIDEOSYSTEM = {
		0 : '1920x1080p60',
		1 : '1920x1080p30',
		2 : '1920x1080i60',
		3 : '1280x720p60',
		4 : '1280x720p30',
		5 : '640x480p60',
		8 : '1920x1080p50',
		9 : '1920x1080p25',
		10 : '1920x1080i50',
		11 : '1280x720p50',
		12 : '1280x720p25',
		}
		
	def decode_videosystem(self, rv):
		return self.VIDEOSYSTEM.get(rv, 'Unknown (0x%x)' % rv)
		
	# POWER control

	def cmd_cam_power(self,device,onoff):
		if onoff:
			pwr='\x00\x02'
		else:
			pwr='\x00\x03'
		return self.cmd_cam(device,pwr)

	def cmd_cam_power_on(self,device):
		return self.cmd_cam_power(device,True)

	def cmd_cam_power_off(self,device):
		return self.cmd_cam_power(device,False)


	def cmd_cam_auto_power_off(self,device,time=0):
		"""
		time = minutes without command until standby
		0: disable
		0xffff: 65535 minutes
		"""
		subcmd='\x40'+self.i2v(time)

		return self.cmd_cam(device,subcmd)


	# ZOOM control

	def cmd_cam_zoom_stop(self,device):
		subcmd="\x07\x00"
		return self.cmd_cam(device,subcmd)

	def cmd_cam_zoom_tele(self,device):
		subcmd="\x07\x02"
		return self.cmd_cam(device,subcmd)

	def cmd_cam_zoom_wide(self,device):
		subcmd="\x07\x03"
		return self.cmd_cam(device,subcmd)


	def cmd_cam_zoom_tele_speed(self,device,speed):
		"""
		zoom in with speed = 0..7
		"""
		sbyte=0x20+(speed&0b111)
		subcmd="\x07"+chr(sbyte)
		return self.cmd_cam(device,subcmd)

	def cmd_cam_zoom_wide_speed(self,device,speed):
		"""
		zoom in with speed = 0..7
		"""
		sbyte=0x30+(speed&0b111)
		subcmd="\x07"+chr(sbyte)
		return self.cmd_cam(device,subcmd)

	def cmd_cam_zoom_direct(self,device,zoom):
		"""
		zoom to value
		optical: 0..4000
		digital: 4000..7000 (1x - 4x)
		"""
		subcmd="\x47"+self.i2v(zoom)
		return self.cmd_cam(device,subcmd)

	#Digital Zoom control on/off
	def cmd_cam_dzoom(self,device,state):
		if state:
			subcmd="\x06\x02"
		else:
			subcmd="\x06\x03"

		return self.cmd_cam(device,subcmd)

	def cmd_cam_dzoom_on(self,device):
		return self.cmd_cam_dzoom(device,True)

	def cmd_cam_dzoom_off(self,device):
		return self.cmd_cam_dzoom(device,False)



#FIXME: CAM_FOCUS COMMANDS
#FIXME: CAM_WB
#FIXME: CAM_?GAIN
#FIXME: CAM_AE
#FIXME: CAM_SlowShutter
#FIXME: CAM_Shutter
#FIXME: CAM_Iris
#FIXME: CAM_Gain
#FIXME: CAM_Bright
#FIXME: CAM_ExpComp
#FIXME: CAM_BackLight
#FIXME: CAM_Aperature

	# 16:9 / Wide format:
	def cmd_cam_wide(self,device,mode):
		subcmd="\x60"+chr(mode)
		return self.cmd_cam(device,subcmd)

	def cmd_cam_wide_off(self,device):
		return self.cmd_cam_wide(device,0x00)

	def cmd_cam_wide_cinema(self,device):
		return self.cmd_cam_wide(device,0x01)

	def cmd_cam_wide_full(self,device):
		return self.cmd_cam_wide(device,0x02)


	# mirror
	def cmd_cam_lr_reverse(self,device,mode):
		subcmd="\x61"+chr(mode)
		return self.cmd_cam(device,subcmd)

	def cmd_cam_lr_reverse_on(self,device):
		return self.cmd_cam_lr_reverse(device,0x02)

	def cmd_cam_lr_reverse_off(self,device):
		return self.cmd_cam_lr_reverse(device,0x03)

	# freeze
	def cmd_cam_freeze(self,device,mode):
		subcmd="\x62"+chr(mode)
		return self.cmd_cam(device,subcmd)

	def cmd_cam_freeze_on(self,device):
		return self.cmd_cam_freeze(device,0x02)

	def cmd_cam_freeze_off(self,device):
		return self.cmd_cam_freeze(device,0x03)



	# Picture Effects
	def cmd_cam_picture_effect(self,device,mode):
		subcmd="\x63"+chr(mode)
		return self.cmd_cam(device,subcmd)

	def cmd_cam_picture_effect_off(self,device):
		return self.cmd_cam_picture_effect(device,0x00)

	def cmd_cam_picture_effect_pastel(self,device):
		return self.cmd_cam_picture_effect(device,0x01)

	def cmd_cam_picture_effect_negart(self,device):
		return self.cmd_cam_picture_effect(device,0x02)

	def cmd_cam_picture_effect_sepa(self,device):
		return self.cmd_cam_picture_effect(device,0x03)

	def cmd_cam_picture_effect_bw(self,device):
		return self.cmd_cam_picture_effect(device,0x04)

	def cmd_cam_picture_effect_solarize(self,device):
		return self.cmd_cam_picture_effect(device,0x05)

	def cmd_cam_picture_effect_mosaic(self,device):
		return self.cmd_cam_picture_effect(device,0x06)

	def cmd_cam_picture_effect_slim(self,device):
		return self.cmd_cam_picture_effect(device,0x07)

	def cmd_cam_picture_effect_stretch(self,device):
		return self.cmd_cam_picture_effect(device,0x08)



	# Digital Effect

	def cmd_cam_digital_effect(self,device,mode):
		subcmd="\x64"+chr(mode)
		return self.cmd_cam(device,subcmd)

	def cmd_cam_digital_effect_off(self,device):
		return self.cmd_cam_digital_effect(device,0x00)

	def cmd_cam_digital_effect_still(self,device):
		return self.cmd_cam_digital_effect(device,0x01)

	def cmd_cam_digital_effect_flash(self,device):
		return self.cmd_cam_digital_effect(device,0x02)

	def cmd_cam_digital_effect_lumi(self,device):
		return self.cmd_cam_digital_effect(device,0x03)

	def cmd_cam_digital_effect_trail(self,device):
		return self.cmd_cam_digital_effect(device,0x04)


	def cmd_cam_digital_effect_level(self,device,level):
		subcmd="\x65"+chr( 0b00111111 & level)
		return self.cmd_cam(device,subcmd)


	# memory of settings including position
	def cmd_cam_memory(self,device,func,num):
		if num>5:
			num=5
		if func<0 or func>2:
			return
		print "DEBUG: cam_memory command"
		subcmd="\x3f"+chr(func)+chr( 0b0111 & num)
		return self.cmd_cam(device,subcmd)


	#FIXME; Can only be executed when motion has stopped!!!
	def cmd_cam_memory_reset(self,device,num):
		return self.cmd_cam_memory(device,0x00,num)

	def cmd_cam_memory_set(self,device,num):
		return self.cmd_cam_memory(device,0x01,num)

	def cmd_cam_memory_recall(self,device,num):
		return self.cmd_cam_memory(device,0x02,num)


	# Datascreen control

	def cmd_datascreen(self,device,func):
		subcmd='\x06'+chr(func)
		return self.cmd_pt(device,subcmd)

	def cmd_datascreen_on(self,device):
		return self.cmd_datascreen(device,0x02)

	def cmd_datascreen_off(self,device):
		return self.cmd_datascreen(device,0x03)

	def cmd_datascreen_toggle(self,device):
		return self.cmd_datascreen(device,0x10)


#FIXME: IR_Receive
#FIXME: IR_Receive_Return


	# Pan and Tilt Drive:

	def cmd_ptd(self,device,ps,ts,lr,ud):

		subcmd='\x01'+chr(ps)+chr(ts)+chr(lr)+chr(ud)
		return self.cmd_pt(device,subcmd)

	def cmd_ptd_up(self,device,ts=0x14):
		return self.cmd_ptd(device,0,ts,0x03,0x01)

	def cmd_ptd_down(self,device,ts=0x14):
		return self.cmd_ptd(device,0,ts,0x03,0x02)

	def cmd_ptd_left(self,device,ps=0x18):
		return self.cmd_ptd(device,ps,0,0x01,0x03)

	def cmd_ptd_right(self,device,ps=0x18):
		return self.cmd_ptd(device,ps,0,0x02,0x03)


	def cmd_ptd_upleft(self,device,ts=0x14,ps=0x18):
		return self.cmd_ptd(device,ps,ts,0x01,0x01)

	def cmd_ptd_upright(self,device,ts=0x14,ps=0x18):
		return self.cmd_ptd(device,ps,ts,0x02,0x01)

	def cmd_ptd_downleft(self,device,ts=0x14,ps=0x18):
		return self.cmd_ptd(device,ps,ts,0x01,0x02)

	def cmd_ptd_downright(self,device,ts=0x14,ps=0x18):
		return self.cmd_ptd(device,ps,ts,0x02,0x02)

	def cmd_ptd_stop(self,device):
		return self.cmd_ptd(device,0,0,0x03,0x03)



	def cmd_ptd_abs(self,device,ts=0x14,ps=0x18,pp=0,tp=0):

		print "DEBUG: ABS POS TO %d/%d" % (pp,tp)

		# pp: range: -1440 - 1440
		if pp<0:
			p=(((pp*-1)-1)^0xffff)
		else:
			p=pp

		#tp: range -360 - 360
		if tp<0:
			t=(((tp*-1)-1)^0xffff)
		else:
			t=tp

		subcmd='\x02'+chr(ts)+chr(ps)+self.i2v(p)+self.i2v(t)
		return self.cmd_pt(device,subcmd)


	def cmd_ptd_home(self,device):
		subcmd='\x04'
		return self.cmd_pt(device,subcmd)

	def cmd_ptd_reset(self,device):
		subcmd='\x05'
		return self.cmd_pt(device,subcmd)


#FIXME: Pan-tiltLimitSet




