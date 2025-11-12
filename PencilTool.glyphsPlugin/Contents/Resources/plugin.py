# encoding: utf-8
###########################################################################################################
#
# Pencil Tool Plugin - v1.0.2 (new: detects stylus input and adjusts drawing sensitivity)
#
###########################################################################################################

from __future__ import division, print_function, unicode_literals
import objc, os, math
from GlyphsApp import Glyphs, GSPath, GSNode, GSOFFCURVE, GSCURVE, GSLINE, GSEditViewController, UPDATEINTERFACE, GSLayer
from GlyphsApp.plugins import SelectTool, PalettePlugin
from Cocoa import NSClassFromString
from Foundation import NSBundle
from AppKit import NSImage, NSColor, NSBezierPath, NSPoint
import os

# ----------------------------------------------------------
# Constantes globales
# ----------------------------------------------------------
DEFAULT_SIMPLIFY_EPSILON = 2.0  # abaissée pour un tracé plus précis
DEFAULT_STROKE_WIDTH = 30.0
MIN_DISTANCE = 4.0

# ----------------------------------------------------------
# Fonctions utilitaires globales
# ----------------------------------------------------------
def distance(p1, p2):
	return math.hypot(p2.x - p1.x, p2.y - p1.y)

def distance_point_segment(p, a, b):
	x, y = p.x, p.y
	x1, y1 = a.x, a.y
	x2, y2 = b.x, b.y
	dx = x2 - x1
	dy = y2 - y1
	if dx == 0 and dy == 0:
		return math.hypot(x - x1, y - y1)
	t = ((x - x1) * dx + (y - y1) * dy) / (dx*dx + dy*dy)
	t = max(0.0, min(1.0, t))
	projx = x1 + t*dx
	projy = y1 + t*dy
	return math.hypot(x - projx, p.y - projy)

def rdp_simplify(points, epsilon):
	if len(points) < 3:
		return points[:]
	dmax = 0.0
	index = 0
	a = points[0]
	b = points[-1]
	for i in range(1, len(points)-1):
		d = distance_point_segment(points[i], a, b)
		if d > dmax:
			index = i
			dmax = d
	if dmax > epsilon:
		left = rdp_simplify(points[:index+1], epsilon)
		right = rdp_simplify(points[index:], epsilon)
		return left[:-1] + right
	else:
		return [points[0], points[-1]]

def ns_add(a, b):
	return NSPoint(a.x + b.x, a.y + b.y)

def ns_sub(a, b):
	return NSPoint(a.x - b.x, a.y - b.y)

def ns_mul(a, s):
	return NSPoint(a.x * s, a.y * s)

def ns_div(a, s):
	return NSPoint(a.x / s, a.y / s)

def b_spline_to_bezier(points):
	n = len(points)
	if n < 2:
		return []
	if n == 2:
		p0, p1 = points
		c1 = NSPoint(p0.x + (p1.x - p0.x)/3, p0.y + (p1.y - p0.y)/3)
		c2 = NSPoint(p0.x + 2*(p1.x - p0.x)/3, p0.y + 2*(p1.y - p0.y)/3)
		return [(p0, c1, c2, p1)]
	padded = [points[0], points[0]] + points[:] + [points[-1], points[-1]]
	beziers = []
	for i in range(len(padded) - 3):
		P0, P1, P2, P3 = padded[i], padded[i+1], padded[i+2], padded[i+3]
		Q0 = ns_div(ns_add(ns_add(P0, ns_mul(P1, 4.0)), P2), 6.0)
		Q1 = ns_div(ns_add(ns_mul(P1, 4.0), ns_mul(P2, 2.0)), 6.0)
		Q2 = ns_div(ns_add(ns_mul(P1, 2.0), ns_mul(P2, 4.0)), 6.0)
		Q3 = ns_div(ns_add(ns_add(P1, ns_mul(P2, 4.0)), P3), 6.0)
		beziers.append((Q0, Q1, Q2, Q3))
	return [seg for seg in beziers if abs(seg[0].x - seg[3].x) > 1e-6 or abs(seg[0].y - seg[3].y) > 1e-6]

# ----------------------------------------------------------
# Palette intégrée : PencilToolVariables
# ----------------------------------------------------------
class PencilToolVariables(PalettePlugin):
	dialog = objc.IBOutlet()
	thicknessSlider = objc.IBOutlet()
	smoothingSlider = objc.IBOutlet()
	thicknessLabel = objc.IBOutlet()
	smoothingLabel = objc.IBOutlet()

	thickness = 30.0
	smoothing = 4

	@objc.python_method
	def settings(self):
		self.name = Glyphs.localize({
			'en': 'Pencil settings',
			'fr': 'Paramètres du crayon',
			'de': 'Bleistift-Einstellungen',
			'es': 'Ajustes del lápiz',
			'zh': '铅笔设置',
			'ja': '鉛筆の設定',
			'pt': 'Configurações do lápis',
			'it': 'Impostazioni della matita',
			'nl': 'Potloodinstellingen',
			'ko': '연필 설정',
			'ru': 'Настройки карандаша',
		})
		self.loadNib('IBdialog', __file__)
		self.dialog.setController_(self)

	@objc.python_method
	def start(self):
		Glyphs.addCallback(self.update, UPDATEINTERFACE)

	@objc.python_method
	def __del__(self):
		Glyphs.removeCallback(self.update)

	def minHeight(self):
		return 120
		
	def maxHeight(self):
		return 120

	@objc.IBAction
	def thicknessChanged_(self, sender):
		self.thickness = round(sender.floatValue())
		if Pencil.instance:
			Pencil.instance.strokeWidth = self.thickness
		self.update(None)

	@objc.IBAction
	def smoothingChanged_(self, sender):
		self.smoothing = round(sender.floatValue())
		if Pencil.instance:
			# Lissage exponentiel : plus la valeur est grande, plus le tracé est simplifié
			Pencil.instance.simplifyEpsilon = DEFAULT_SIMPLIFY_EPSILON * (1.25 ** self.smoothing)
		self.update(None)

	@objc.python_method
	def update(self, sender):
		labels = Glyphs.localize({
			'en': {'thickness_label': 'Thickness:', 'smoothing_label': 'Smoothing:'},
			'fr': {'thickness_label': 'Épaisseur :', 'smoothing_label': 'Lissage :'},
			'de': {'thickness_label': 'Dicke:', 'smoothing_label': 'Glättung:'},
			'es': {'thickness_label': 'Grosor:', 'smoothing_label': 'Suavizado:'},
			'zh': {'thickness_label': '粗细:', 'smoothing_label': '平滑度:'},
			'ja': {'thickness_label': '太さ:', 'smoothing_label': 'スムージング:'},
			'pt': {'thickness_label': 'Espessura:', 'smoothing_label': 'Suavização:'},
			'it': {'thickness_label': 'Spessore:', 'smoothing_label': 'Levigatura:'},
			'nl': {'thickness_label': 'Dikte:', 'smoothing_label': 'Gladmaken:'},
			'ko': {'thickness_label': '두께:', 'smoothing_label': '매끄럽게:'},
			'ru': {'thickness_label': 'Толщина:', 'smoothing_label': 'Сглаживание:'}
		})

		if self.thicknessLabel:
			self.thicknessLabel.setStringValue_(f'{labels["thickness_label"]} {int(self.thickness)}')
		if self.smoothingLabel:
			self.smoothingLabel.setStringValue_(f'{labels["smoothing_label"]} {int(self.smoothing)}')

	@objc.python_method
	def __file__(self):
		return __file__
		
# ----------------------------------------------------------
# Pencil Tool principal
# ----------------------------------------------------------
class Pencil(SelectTool):
	instance = None

	@objc.python_method
	def settings(self):
		self.name = Glyphs.localize({
			'en': 'Pencil',
			'fr': 'Crayon',
			'de': 'Bleistift',
			'es': 'Lápiz',
			'zh': '铅笔',
			'ja': '鉛筆',
			'pt': 'Lápis',
			'it': 'Matita',
			'nl': 'Potlood',
			'ko': '연필',
			'ru': 'Карандаш',
		})

		icon_path = os.path.join(os.path.dirname(__file__), "PencilTool.pdf")
		highlight_path = os.path.join(os.path.dirname(__file__), "PencilToolHighlight.pdf")
		self.default_image = NSImage.alloc().initByReferencingFile_(icon_path)
		self.active_image = NSImage.alloc().initByReferencingFile_(highlight_path)
		self._icon = None  # needs to be set to None for now. Is fixed in 3.4 (3416)
		self.tool_bar_image = self.default_image

		self.toolbarIconName = "PencilTool"

		self.keyboardShortcut = 'X'
		self.toolbarPosition = 181

		self.strokeWidth = DEFAULT_STROKE_WIDTH
		self.simplifyEpsilon = DEFAULT_SIMPLIFY_EPSILON
		self.minDistance = MIN_DISTANCE
		self.roundCaps = True

		Pencil.instance = self

	@objc.python_method
	def start(self):
		self.points = []
		self.lastPoint = None

	@objc.python_method
	def activate(self):
		# icône active
		self.tool_bar_image = self.active_image

	@objc.python_method
	def deactivate(self):
		# icône inactive
		self.tool_bar_image = self.default_image

	# -----------------------
	# Méthodes utilitaires de classe
	# -----------------------
	@objc.python_method
	def remove_duplicate_points(self, points):
		if not points:
			return []
		cleaned = [points[0]]
		for pt in points[1:]:
			if pt.x != cleaned[-1].x or pt.y != cleaned[-1].y:
				cleaned.append(pt)
		return cleaned

	@objc.python_method
	def remove_duplicate_nodes(self, nodes):
		if not nodes:
			return []
		cleaned = [nodes[0]]
		for node in nodes[1:]:
			if node.position.x != cleaned[-1].position.x or node.position.y != cleaned[-1].position.y:
				cleaned.append(node)
		return cleaned

	@objc.python_method
	def remove_close_nodes(self, nodes, threshold=1.0):
		if not nodes:
			return []
		cleaned = [nodes[0]]
		for node in nodes[1:]:
			dx = node.position.x - cleaned[-1].position.x
			dy = node.position.y - cleaned[-1].position.y
			if math.hypot(dx, dy) >= threshold:
				cleaned.append(node)
		return cleaned

	# -----------------------
	# Événements souris
	# -----------------------
	def mouseDown_(self, theEvent):
		view = self.editViewController().graphicView()
		loc = view.getActiveLocation_(theEvent)
		self.points = [loc]
		self.lastPoint = loc

		# --- Détection du type d'entrée ---
		self.usingStylus = False
		try:
			if hasattr(theEvent, "pressure"):
				pressure = theEvent.pressure()

				if 0.0 < pressure < 1.0:
					self.usingStylus = True
			elif hasattr(theEvent, "tabletPointingDeviceType"):
				devType = theEvent.tabletPointingDeviceType()
				# 1 = Pen, 2 = Cursor, 3 = Eraser
				if devType in (1, 3):
					self.usingStylus = True
		except Exception as e:
			print("Device detection failed:", e)

		print("🖊️ Input device:", "Stylus" if self.usingStylus else "Mouse/Trackpad")

		# Ajustement des paramètres en fonction du périphérique
		if self.usingStylus:
			self.minDistance = 2.0
		else:
			self.minDistance = 4.0
		view.setNeedsDisplay_(True)

	def mouseDragged_(self, theEvent):
		if not self.lastPoint:
			return
		view = self.editViewController().graphicView()
		loc = view.getActiveLocation_(theEvent)
		if distance(self.lastPoint, loc) >= self.minDistance:
			self.points.append(loc)
			self.lastPoint = loc
			view.setNeedsDisplay_(True)

	def mouseUp_(self, theEvent):
		objc.super(Pencil, self).mouseUp_(theEvent)
		view = self.editViewController().graphicView()
		if len(self.points) < 2:
			self.points = []
			self.lastPoint = None
			view.setNeedsDisplay_(True)
			return

		layer = view.activeLayer()
		if not layer:
			print("Pencil Tool: No active layer found.")
			return

		# --- build path from points ---
		path = GSPath()
		path.closed = False

		simplified_points = rdp_simplify(self.points, self.simplifyEpsilon)
		simplified_points = self.remove_duplicate_points(simplified_points)
		if len(simplified_points) < 2:
			simplified_points = self.points[:]

		beziers = b_spline_to_bezier(simplified_points)
		if not beziers:
			for pt in simplified_points:
				path.nodes.append(GSNode(pt, type=GSLINE))
		else:
			first = True
			for p0, c1, c2, p1 in beziers:
				if first:
					path.nodes.append(GSNode(p0, type=GSLINE))
					path.nodes[-1].smooth = True
					path.nodes.append(GSNode(c1, type=GSOFFCURVE))
					path.nodes.append(GSNode(c2, type=GSOFFCURVE))
					path.nodes.append(GSNode(p1, type=GSCURVE))
					path.nodes[-1].smooth = True
					first = False
				else:
					path.nodes.append(GSNode(c1, type=GSOFFCURVE))
					path.nodes.append(GSNode(c2, type=GSOFFCURVE))
					path.nodes.append(GSNode(p1, type=GSCURVE))
					path.nodes[-1].smooth = True

		# --- round coordinates of the initial path ---
		for node in path.nodes:
			node.position = NSPoint(round(node.position.x), round(node.position.y))

		# --- temporary layer for Offset (thickness) ---
		tempLayer = GSLayer()
		tempLayer.width = layer.width
		tempLayer.paths.append(path)

		# --- apply OffsetCurve to give thickness ---
		try:
			offsetFilter = NSClassFromString("GlyphsFilterOffsetCurve")
			offsetFilter.offsetLayer_offsetX_offsetY_makeStroke_autoStroke_position_metrics_error_shadow_capStyleStart_capStyleEnd_keepCompatibleOutlines_(
				tempLayer,
				self.strokeWidth / 2.0, self.strokeWidth / 2.0,
				True, False, 0.5,
				None, None, None,
				0, 0, False
			)
		except Exception as e:
			print(f"Pencil Tool - Offset failed: {e}")

		# --- temporary layer for Roughenizer (copy of thickened paths) ---
		tempLayer2 = GSLayer()
		tempLayer2.width = layer.width
		for p in tempLayer.paths:
			tempLayer2.paths.append(p.copy())

		# --- draw circles at start and end points (fusionnés avec le tracé) ---
		def drawCircle(position, radius):
			MAGICNUMBER = 4.0 * (2.0**0.5 - 1.0) / 3.0
			handle = MAGICNUMBER * radius
			x = position.x
			y = position.y
			myCoordinates = (
				(NSPoint(x - handle, y + radius), NSPoint(x - radius, y + handle), NSPoint(x - radius, y)),
				(NSPoint(x - radius, y - handle), NSPoint(x - handle, y - radius), NSPoint(x, y - radius)),
				(NSPoint(x + handle, y - radius), NSPoint(x + radius, y - handle), NSPoint(x + radius, y)),
				(NSPoint(x + radius, y + handle), NSPoint(x + handle, y + radius), NSPoint(x, y + radius)),
			)
			circlePath = GSPath()
			circlePath.nodes.append(GSNode(NSPoint(x, y + radius), type=GSLINE)) # Point de départ
			for segment in myCoordinates:
				for handlePos in segment[:2]:
					node = GSNode(handlePos, type=GSOFFCURVE)
					circlePath.nodes.append(node)
				node = GSNode(segment[2], type=GSCURVE)
				circlePath.nodes.append(node)
			circlePath.closed = True
			return circlePath

		radius = self.strokeWidth / 2.0
		if len(path.nodes) >= 2:
			startCircle = drawCircle(path.nodes[0].position, radius)
			endCircle = drawCircle(path.nodes[-1].position, radius)
			tempLayer2.paths.append(startCircle)
			tempLayer2.paths.append(endCircle)

		# --- apply Roughenizer ---
		try:
			roughenFilter = NSClassFromString("GlyphsFilterRoughenizer").alloc().init()
			roughenFilter.roughenLayer_segmentLength_offsetX_offsetY_angle_shadowLayer_error_(
				tempLayer2,
				10.0, 2.0, 2.0, 1.0, None, None
			)
		except Exception as e:
			print(f"Pencil Tool - Roughen failed: {e}")

		# --- ARRONDIR LES COORDONNÉES DES NŒUDS ---
		for p in tempLayer2.paths:
			for node in p.nodes:
				node.position = NSPoint(round(node.position.x), round(node.position.y))
				
		# --- apply Remove Overlap on entire active path (tracé + cercles) ---
		# Cette étape peut générer des nœuds superflus
		try:
			tempLayer2.removeOverlap()
		except Exception:
			try:
				removeOverlapFilter = NSClassFromString("GlyphsFilterRemoveOverlap")
				removeOverlapFilter.applyFilterToLayer_(tempLayer2)
			except Exception as e:
				print(f"Pencil Tool - removeOverlap fallback failed: {e}")

		# --- NETTOYAGE FINAL AMÉLIORÉ ET SUPPRESSION DES ÎLOTS (CRUCIAL) ---
		cleaned_paths = []
		min_path_length = self.strokeWidth * 3
		
		for p in tempLayer2.paths:
			# 1. Nettoyage des nœuds : supprime les doublons et les nœuds trop proches (<= 1 unité)
			p.nodes = self.remove_duplicate_nodes(p.nodes)
			p.nodes = self.remove_close_nodes(p.nodes, threshold=1.0)
			
			# 2. Vérification de la taille pour supprimer les îlots/artefacts
			if len(p.nodes) >= 3:
				path_length = 0
				try:
					# Calculer la longueur approximative du contour
					nodes_to_check = [n for n in p.nodes if n.type != GSOFFCURVE]
					if len(nodes_to_check) > 1:
						for i in range(len(nodes_to_check)):
							n1 = nodes_to_check[i]
							n2 = nodes_to_check[(i + 1) % len(nodes_to_check)]
							path_length += distance(n1.position, n2.position)
				except Exception:
					pass
				
				if path_length > min_path_length:
					cleaned_paths.append(p)

		# Remplacer les chemins de la couche temporaire par les chemins nettoyés
		# CORRECTION finale de l'API : nous recréons une couche pour garantir qu'elle est vide.
		# Note: La variable tempLayer2 est réutilisée ci-dessous.
		tempLayer2 = GSLayer()
		tempLayer2.width = layer.width
		tempLayer2.paths.extend(cleaned_paths)


		# --- add paths to real layer ---
		try:
			for p in tempLayer2.paths:
				layer.paths.append(p)
		except Exception as e:
			print(f"Pencil Tool - Could not append paths: {e}")

		# --- cleanup ---
		self.points = []
		self.lastPoint = None
		view.setNeedsDisplay_(True)
		Glyphs.redraw()
		
	@objc.python_method
	def background(self, layer):
		simplified_points = rdp_simplify(self.points, self.simplifyEpsilon)
		if len(simplified_points) < 2:
			return
		color = NSColor.blackColor().colorWithAlphaComponent_(0.4)
		color.set()
		bezier = NSBezierPath.bezierPath()
		bezier.setLineWidth_(self.strokeWidth)
		bezier.setLineCapStyle_(1)
		beziers = b_spline_to_bezier(simplified_points)
		if not beziers:
			return
		bezier.moveToPoint_(beziers[0][0])
		for p0, c1, c2, p1 in beziers:
			bezier.curveToPoint_controlPoint1_controlPoint2_(p1, c1, c2)
		bezier.stroke()

	@objc.python_method
	def __file__(self):
		return __file__