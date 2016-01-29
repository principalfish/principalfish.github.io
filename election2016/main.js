//map object
var map = {
  width: 960,
  height: 500,
  active : d3.select(null),

  projection : function(){
      return d3.geo.albersUsa()
    .scale(1000)
    .translate([this.width / 2, this.height / 2]);
  },

  zoom : function(){
      return d3.behavior.zoom()
        .translate([0, 0])
        .scale(1)
        .scaleExtent([1, 8])
        .on("zoom", this.zoomed);
  },

  path : function(){
      return d3.geo.path()
        .projection(this.projection());
  },

  // stops users messing up zoom animation
  disableZoom: function(){
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
    return svg.call(map.zoom());
  },

  // If the drag behavior prevents the default click,
  // also stop propagation so we don’t click-to-zoom.
  stopped: function() {
    if (d3.event.defaultPrevented) d3.event.stopPropagation();
  },

  zoomed: function() {
    g.style("stroke-width", 1.5 / d3.event.scale + "px");
    g.attr("transform", "translate(" + d3.event.translate + ")scale(" + d3.event.scale + ")");
  },

  reset: function() {

    map.active.classed("active", false);
    map.active = d3.select(null);

    map.disableZoom();

  	svg.transition()
  		.duration(1500)
  		.call(map.zoom().translate([0, 0]).scale(1).event)
  		.each("end", map.enableZoom);
    },

  clicked : function(d) {
    if (map.active.node() === this) return map.reset();
    map.active.classed("active", false);
    map.active = d3.select(this).classed("active", true);

    $("#text").text(d.properties.name)

    var bounds = map.path().bounds(d),
  		dx = Math.pow((bounds[1][0] - bounds[0][0]), 0.5),
  		dy = Math.pow((bounds[1][1] - bounds[0][1]), 0.5),
  		x = (bounds[0][0] + bounds[1][0]) / 2,
  		y = (bounds[0][1] + bounds[1][1]) / 2,

  		scale = .075 / Math.max(dx /  map.width,  dy / map.height),
  		translate = [map.width / 2 - scale * x, map.height / 2 - scale * y];

  	map.disableZoom();

    svg.transition()
      .duration(1500)
      .call(map.zoom().translate(translate).scale(scale).event)
      .each("end", map.enableZoom);
  }
};

var pageData = {
  states : [],
  stateData: {},

  loadmap : function(){

    d3.json("data/map.topojson", function(error, us) {
      if (error) throw error;

      g.selectAll(".map")
          .data(topojson.feature(us, us.objects.collection).features)

        .enter().append("path")
          .attr("d", map.path())
          .attr("class", "state")
          .each(function(d){
            (pageData.states).push(d.properties.name)
          })

          .on("click", map.clicked);

      g.append("path")
          .datum(topojson.mesh(us, us.objects.collection, function(a, b) { return a !== b; }))
          .attr("class", "boundaries")
          .attr("d", map.path());
    });
  },

  getData: function(url){

  	return $.ajax({
  		cache: true,
  		dataType: "json",
    	url: url,
  		type: "GET",
  	});
  },

  getSeatInfo: function(data){

    $.each(data, function(seat){
  		pageData.stateData[seat] = data[seat];

  	});

  	pageData.loadmap();
  },

  loadTheMap: function(url){
  	pageData.stateData = {};

  	$(document).ready(function(){ pageData.getData("data/" + "info.json").done(pageData.getSeatInfo)});

  }
};

var svg = d3.select("#map").append("svg")
    .attr("width", map.width)
    .attr("height", map.height)
    .on("click", map.stopped, true);

svg.append("rect")
    .attr("class", "background")
    .attr("width", map.width)
    .attr("height", map.height)
    .on("click", map.reset);

var g = svg.append("g");

svg
    .call(map.zoom()) // delete this line to disable free zooming
    .call(map.zoom().event);

pageData.loadTheMap();
