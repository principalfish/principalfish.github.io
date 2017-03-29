var mapAttr = {
  "width" : 600,
  "height" : 875,
  "active" : d3.select(null),

	// nodes for various  map ui changes (flash seat/choropleths etc)
	"activeNode" : null,
	//when map clicked on function
	clicked : function(d){

		mapAttr.activeNode = d;

		// flash seat on click
		mapAttr.flashSeat(d);

		var bounds = path.bounds(d),
			dx = Math.pow((bounds[1][0] - bounds[0][0]), 0.5),
			dy = Math.pow((bounds[1][1] - bounds[0][1]), 0.5),
			x = (bounds[0][0] + bounds[1][0]) / 2,
			y = (bounds[0][1] + bounds[1][1]) / 2,

			scale = .05 / Math.max(dx /  mapAttr.width,  dy /  mapAttr.height),
			translate = [ mapAttr.width / 2 - scale * x,  mapAttr.height / 2 - scale * y];

		mapAttr.disableZoom();

		svg.transition()
				.duration(1500)
				.call(zoom.transform, d3.zoomIdentity.translate(translate[0],translate[1]).scale(scale))
				.on("end", mapAttr.enableZoom);

    uiAttr.showDiv("seat-information")
		seatInfoTable.display(d.properties.name);
	},

	// flash seat on click - reset previous
	flashSeat: function(node){

		var mapID = "#i" + node.properties.info_id;
		current = d3.select(mapID);

		//var opacity = $(mapID).css("opacity");

		repeat();

		function repeat(){
			current
				.transition()
					.duration(1500)
					.attr("opacity", 0.02)
				.transition()
					.duration(1500)
					.attr("opacity", node.opacity)
				.on("end", repeat);
		}

	},

	//stops users messing up zoom animations
	disableZoom : function(){

		svg.on("mousedown.zoom", null);
		svg.on("mousemove.zoom", null);
		svg.on("dblclick.zoom", null);
		svg.on("touchstart.zoom", null);
		svg.on("wheel.zoom", null);
		svg.on("mousewheel.zoom", null);
		svg.on("MozMousePixelScroll.zoom", null);

	},

	// reenables users ability to zoom pan etc
	enableZoom : function(){
		svg.call(zoom);
	},

	// reset button for map
	reset : function() {

		//stop previous seat flashing
		if (mapAttr.activeNode != null){
			d3.select("#i" + mapAttr.activeNode.properties.info_id)
			.transition()
			.attr("opacity", mapAttr.activeNode.opacity);
		}

		mapAttr.activeNode = null;

		mapAttr.disableZoom();

		svg.transition()
			.duration(1500)
			.call(zoom.transform, d3.zoomIdentity)
			.on("end", function(){mapAttr.enableZoom()});
	},

	// zoom function
	zoomed : function() {
		g.style("stroke-width", 1.5 / d3.event.scale + "px");
		g.attr("transform", d3.event.transform);
	},

	stopped : function() {
		if (d3.event.defaultPrevented) {
			d3.event.stopPropagation();
		}
	},

	// load + colour map at page load
	loadmap : function(setting){


		// d3.json(setting.mapurl, function(error, uk) {
		// 	if (error) return console.error(error);

			g.selectAll(".map")
				.data(topojson.feature(setting.polygons, setting.polygons.objects.map).features)
				.enter().append("path")
				.attr("class", function(d){
					var seatClass;
					if (d.properties.name in setting.seatData){
						 seatClass = "map " + setting.seatData[d.properties.name]["seatInfo"]["current"];
					} else {
						seatClass = "map null"
					}
					return seatClass
				})
				.attr("opacity", 1)
				.attr("id", function(d) {
					return "i" + d.properties.info_id;
					})
				.attr("d", path)
				.on("click", mapAttr.clicked)
				.append("svg:title")
					.text(function(d) { return d.properties.name})
				.each(function(d){
					if (d.properties.name in currentMap.seatData){

						// for finding seat from various search features
						setting.seatData[d.properties.name]["mapSelect"] = d;
						// set current opacity - for flashseat and choropleths //
						setting.seatData[d.properties.name]["mapSelect"]["opacity"]	= 1;
						// set opacity = 1 for filters
						filters.opacities[d.properties.name] = 1;
						// lset filtreed to true
						setting.seatData[d.properties.name].filtered = true;
						// get turnout
						var turnout = 0;
						for (var party in setting.seatData[d.properties.name].partyInfo){
							turnout += setting.seatData[d.properties.name].partyInfo[party].total;
						}
						setting.seatData[d.properties.name].seatInfo.turnout = turnout;

						// get previous turnout
						var previousTurnout = 0;

						for (var party in setting.previousSeatData[d.properties.name].partyInfo){
							previousTurnout += setting.previousSeatData[d.properties.name].partyInfo[party].total;
						}
						setting.previousSeatData[d.properties.name].seatInfo.turnout = previousTurnout;
					}
				});

			g.append("path")
				.datum(topojson.mesh(setting.polygons, setting.polygons.objects.map, function(a, b){
					return a.properties.region != b.properties.region && a != b; }))
				.attr("d", path)
				.attr("class", "boundaries");
		// });
	}

}

var projection = d3.geoAlbers() // variables to change default map position
	.center([-0.5, 54.4]) //centers vertically and horizontally
	.rotate([2.25, 0])
	.parallels([51, 60])
	.scale(5300)
	.translate([mapAttr.width / 2, mapAttr.height / 2])


var path = d3.geoPath().projection(projection);

var zoom = d3.zoom()
  .scaleExtent([1, 8])
  .on("zoom", mapAttr.zoomed);
