var regionData = {};

var maxDensity = 2667;

function loadmap(){
  d3.json("data/map.topojson", function(italy) {



    g.selectAll("path")
        .data(topojson.feature(italy, italy.objects.italy).features)
      .enter().append("path")
        .attr("d", path)
        .each(function(d){regionData[d.properties.NAME] = d.properties})
        .attr("class", "map")
        .style("fill", "lightgreen")
        .style("opacity", function(d) { return d.properties.POP_DENSIT / 1200;})
        .on("click", clicked);
  });


}

function clicked(d) {
  if (active.node() === this) return reset();
  active.classed("active", false);
  active = d3.select(this).classed("active", true);

  var bounds = path.bounds(d),
      dx = bounds[1][0] - bounds[0][0],
      dy = bounds[1][1] - bounds[0][1],
      x = (bounds[0][0] + bounds[1][0]) / 2,
      y = (bounds[0][1] + bounds[1][1]) / 2,
      scale = 0.5 / Math.max(dx / width, dy / height),
      translate = [width / 2 - scale * x, height / 3 - scale * y];

  disableZoom();

  svg.transition()
      .duration(750)
      .call(zoom.translate(translate).scale(scale).event)
      .each("end", enableZoom);;

  $("#seat-information").show();
  seatinfo(d);

}

function reset() {
  active.classed("active", false);
  active = d3.select(null);

  svg.transition()
      .duration(750)
      .call(zoom.translate([0, 0]).scale(1).event)
      .each("end", enableZoom);;

  $("#seat-information").hide();
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

function zoomed() {
  g.style("stroke-width", 1.5 / d3.event.scale + "px");
  g.attr("transform", "translate(" + d3.event.translate + ")scale(" + d3.event.scale + ")");
}

// If the drag behavior prevents the default click,
// also stop propagation so we don’t click-to-zoom.
function stopped() {
  if (d3.event.defaultPrevented) d3.event.stopPropagation();
}

loadmap();
