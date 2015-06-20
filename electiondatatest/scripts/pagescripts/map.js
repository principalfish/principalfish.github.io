function zoomToClickedFilteredSeat(d){

	var id = "#i" + d.properties.info_id;

	if (previousnode != undefined){
		var previous_seat = seatsFromIDs[previousnode];
		previous_opacity = seatData[previous_seat]["seat_info"]["current_colour"];
		}

	if (previous_opacity == undefined){
		previous_opacity = 1;
	}

	var current_seat = seatsFromIDs[id];
	current_colour = seatData[current_seat]["seat_info"]["current_colour"];

	if (current_colour == undefined){
		current_colour = 1;
	}

	previous = d3.select(previousnode);
	current = d3.select(id);

	flashSeat(previous, current, previous_opacity, current_colour);

	var bounds = path.bounds(d),
		dx = Math.pow((bounds[1][0] - bounds[0][0]), 0.5),
		dy = Math.pow((bounds[1][1] - bounds[0][1]), 0.5),
		x = (bounds[0][0] + bounds[1][0]) / 2,
		y = (bounds[0][1] + bounds[1][1]) / 2,

		scale = .025 / Math.max(dx /  width,  dy / height),
		translate = [width / 2 - scale * x, height / 3 - scale * y];

	disableZoom();

	svg.transition()
			.duration(1500)
			.call(zoom.translate(translate).scale(scale).event)
			.each("end", enableZoom);


	$("#seat-information").show();
	seatinfo(d);
	previousnode = id;
}

function flashSeat(previous, current, previous_opacity, current_colour, optional){

	if (optional == undefined){
		repeat();
	}
	previous.transition()
		.attr("opacity", previous_opacity);
	console.log(previous_opacity)
	// flashes selected seat on map
	function repeat(){
		current
			.transition()
				.duration(1500)
				.attr("opacity", 0.2)
			.transition()
				.duration(1500)
				.attr("opacity", current_colour)
			.each("end", repeat);
		}
}

// stops users messing up zoom animation
function disableZoom(){
	svg.on("mousedown.zoom", null);
	svg.on("mousemove.zoom", null);
	svg.on("dblclick.zoom", null);
	svg.on("touchstart.zoom", null);
	svg.on("wheel.zoom", null);
	svg.on("mousewheel.zoom", null);
	svg.on("MozMousePixelScroll.zoom", null);
}

// reenables users ability to zoom pan etc
function enableZoom(){
	svg.call(zoom);
}

// reset button for map
function reset() {
	if ($("#seat-information").is(":visible")){
		$("#seat-information").hide();
		}
	disableZoom();
	svg.transition()
		.duration(1500)
		.call(zoom.translate([0, 0]).scale(1).event)
		.each("end", enableZoom);
}

// zoom function
function zoomed() {
	g.style("stroke-width", 1.5 / d3.event.scale + "px");
	g.attr("transform", "translate(" + d3.event.translate + ")scale(" + d3.event.scale + ")");
}

function stopped() {
	if (d3.event.defaultPrevented) d3.event.stopPropagation();
}


// load + colour map at page load
function loadmap(){

	d3.json("data/map.json", function(error, uk) {
		if (error) return console.error(error);

		g.selectAll(".map")
			.data(topojson.feature(uk, uk.objects.map).features)
			.enter().append("path")
			.attr("class", function(d) {
				seatname = d.properties.name

				if (seatname in seatData){
					seatParty = seatData[seatname]["seat_info"]["winning_party"]
					return "map " + seatParty
				}
				else {
					return "not_here"
				}
			})
			.attr("opacity", 1)
			.attr("id", function(d) {
				return "i" + d.properties.info_id
				})
			.attr("d", path)
			.on("click", zoomToClickedFilteredSeat)
			.append("svg:title")
				.text(function(d) { return d.properties.name})
			.each(function(d){

				searchSeatData.push(d);
				seatNames.push(d.properties.name);
				seatsFromIDs["#i" + d.properties.info_id] = d.properties.name
				seatData[d.properties.name]["seat_info"]["info_id"] = "#i" + d.properties.info_id;
				if (d.properties.name in seatData){
					seatsAfterFilter.push(d);
					seatDataForChoropleth[d.properties.name] = seatData[d.properties.name]
					}
			});

		g.append("path")
			.datum(topojson.mesh(uk, uk.objects.map, function(a, b){
				return seatData[a.properties.name]["seat_info"]["area"] != seatData[b.properties.name]["seat_info"]["area"] && a != b; }))
			.attr("d", path)
			.attr("class", "boundaries");
	});
}
