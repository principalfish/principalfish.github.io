// fills out table of info at top of #right
var oldPartyClass = null;
var oldIncumbentClass = null;



function seatinfo(d){

	var relevantData = regionData[d.properties.NAME]

	$("#information-region-span").text(relevantData["NAME"]);
	$("#region-population").text("Population: " + relevantData["POPULATION"]);
	$("#region-density").text("Density: " + relevantData["POP_DENSIT"] + " people per square kilometre")

	horizontalBarChart(relevantData)

	$("#information-pie").html(piechart(relevantData));

}

function horizontalBarChart(data){

	$("#social").empty();
	$("#social").html("<div id=\"region-genders\"></div>")
	var males = data.MALES;
	var females = data.FEMALES;

	var total = males + females;

	var data = {"&#9794" : (100 * males / total).toFixed(1), "&#9792" : (100 * females / total).toFixed(1)};
	var colours = ["darkgreen", "darkblue"];
	var text_colours = ["white", "white"];

	var current_width = 0;
	var count = 0
	$.each(data, function(d, i){
		var x = current_width
		$("#region-genders").append("<div style=\" position: absolute; float: left; margin-left: 21px; background-color :"
										+ colours[count] + "; color: "
										+ text_colours[count] + "; font-size: 1.20em; text-align: center; width :"
										+ 7.5 * data[d] +
										"px; height: 15px; left : " + current_width + "px; top: 65px;\">" +
										d + ":" + i +  "%</div>");

		current_width +=  7.5 * data[d]
		count += 1
	});
}

function piechart(d){
	$("#information-pie").empty();
	$("#information-chart").empty();
	$("#information-chart").html("<table></table>");

	var barchartdata = [];
	barchartdata.push({ageRange : "0 to 14", total : d["AGE_0_14"] });
	barchartdata.push({ageRange : "15 to 29", total : d["AGE_15_29"] });
	barchartdata.push({ageRange : "30 to 44", total : d["AGE_30_44"] });
	barchartdata.push({ageRange : "45 to 59", total : d["AGE_45_59"] });
	barchartdata.push({ageRange : "60 plus", total : d["AGE_60_"] });

	var dataitems = barchartdata.length
	var margin = {top: 40, right: 0, bottom: 40, left:70};

	var width = 350 - margin.left - margin.right;
	var height = 225 - margin.top - margin.bottom;
	var bargap = 2;
	var barwidth = d3.min([100, (width / dataitems) - bargap]);
	var animationdelay = 250;

	var svg1 = d3.select("#information-pie")
		.append("svg")
			.attr("width", width + margin.left + margin.right)
			.attr("height", height + margin.top + margin.bottom)
		.append("g")
    	.attr("transform", "translate(" + margin.left + "," + margin.top + ")");

	// var x = d3.scale.ordinal().range([0, barwidth + bargap, 2 * (barwidth + bargap),
	// 	3  *(barwidth + bargap), 4 * (barwidth + bargap), 5 * (barwidth + bargap)]);

	var x = d3.scale.ordinal().rangeRoundBands([0, width], .1);

	var xAxis = d3.svg.axis()
	    .scale(x)
	    .orient("bottom");

	var y = d3.scale.linear()
		.range([height, 0])

	var yAxis = d3.svg.axis()
    .scale(y)
    .orient("left")
    .ticks(6);

	var round = 25000;

	var count = 0
	var colours = ["#ffcccc", "#ff9999","#ff6666","#ff3232","#ff0000"]

	var max_of_votes = d3.max(barchartdata, function(d) { return d.total; })
	var maxY = round * Math.round(max_of_votes / round) + 25000;

	y.domain([0, maxY]);
	x.domain(barchartdata.map(function(d) { return d.ageRange; }));

	svg1.append("g")
      .attr("class", "x axis")
      .attr("transform", "translate(0," + height + ")")
      .call(xAxis)
		.append("text")
      .attr("x", 225)
			.attr("y", -height - 15)
      .attr("dy", ".71em")
      .style("text-anchor", "end")
      .text("Age Distribution in Region");


	svg1.append("g")
    .attr("class", "y axis")
    .call(yAxis);

	svg1.selectAll("rect")
		.data(barchartdata)
		.enter()
		.append("rect")
      .attr("x", function(d, i) { return bargap * (i + 1) + barwidth * (i); })
      .attr("width", barwidth)
			.attr("y", height)
      .attr("height", 0)
			.style("fill", function(){ count += 1; return colours[count - 1];})
		.transition()
      .delay(function(d, i) { return i * animationdelay / 2; })
      .duration(animationdelay)
      .attr("y", function(d) { return y(d.total); })
      .attr("height", function(d) { return height - y(d.total); });


	$.each(barchartdata, function(i){

		if (parseFloat(barchartdata[i].total) > 0) {

			var to_add = "<tr><td style=\"max-width: 50px;\">" +
			[barchartdata[i].ageRange] + "</td><td>" + (parseFloat(barchartdata[i].total)) +  "</td><tr>";
			}


		$("#information-chart table").append(to_add);
	})

}
