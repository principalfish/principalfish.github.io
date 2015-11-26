var states = [];
var stateData = {};

function loadmap(){
  d3.json("data/map.topojson", function(error, us) {
    if (error) throw error;

    g.selectAll(".map")
        .data(topojson.feature(us, us.objects.collection).features)

      .enter().append("path")
        .attr("d", path)
        .attr("class", "state")
        .each(function(d){
          states.push(d.properties.name)
        })

        .on("click", clicked);

    g.append("path")
        .datum(topojson.mesh(us, us.objects.collection, function(a, b) { return a !== b; }))
        .attr("class", "boundaries")
        .attr("d", path);
  });
}


function clicked(d) {
  if (active.node() === this) return reset();
  active.classed("active", false);
  active = d3.select(this).classed("active", true);

  $("#text").text(d.properties.name)

  var bounds = path.bounds(d),
		dx = Math.pow((bounds[1][0] - bounds[0][0]), 0.5),
		dy = Math.pow((bounds[1][1] - bounds[0][1]), 0.5),
		x = (bounds[0][0] + bounds[1][0]) / 2,
		y = (bounds[0][1] + bounds[1][1]) / 2,

		scale = .075 / Math.max(dx /  width,  dy / height),
		translate = [width / 2 - scale * x, height / 2 - scale * y];

	disableZoom();

  svg.transition()
    .duration(1500)
    .call(zoom.translate(translate).scale(scale).event)
    .each("end", enableZoom);
}

function reset() {
  active.classed("active", false);
  active = d3.select(null);

  disableZoom();

	svg.transition()
		.duration(1500)
		.call(zoom.translate([0, 0]).scale(1).event)
		.each("end", enableZoom);
  }

function zoomed() {
  g.style("stroke-width", 1.5 / d3.event.scale + "px");
  g.attr("transform", "translate(" + d3.event.translate + ")scale(" + d3.event.scale + ")");
}

// If the drag behavior prevents the default click,
// also stop propagation so we don’t click-to-zoom.
function stopped() {
  if (d3.event.defaultPrevented) d3.event.stopPropagation();
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

function getData(url){

	return $.ajax({
		cache: true,
		dataType: "json",
  	url: url,
		type: "GET",
	});
}


function getSeatInfo(data){

  $.each(data, function(seat){
		stateData[seat] = data[seat];

	});

	loadmap();
}

function loadTheMap(url){
	stateData = {};

	$(".map").remove();
	$(document).ready(function(){ getData("data/" + "info.json").done(getSeatInfo)});

}

loadTheMap();
