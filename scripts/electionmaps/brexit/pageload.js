function getData(setting){

	$.when(
		$.getJSON(setting.mapurl, function(data){
			setting.polygons = data;
		}),
		$.getJSON(setting.dataurl, function(data){
			setting.seatData = data;
			currentMap = setting;
			//mapAttr.loadmap(currentMap);
		})

	).then(function(){
		pageLoadEssentials();
	})
};

function pageLoadEssentials(){
	// function does all the random crap that needs changing / resetting on pageload

	// laod the map
	mapAttr.loadmap(currentMap);
	// reset filters.state - also get and show vote totals
	filters.opacities = {};
	filters.reset();

	//key on map
	choro.keyOnMap();

	// populate seat search	// autocomplete function
	var seatList = Object.keys(currentMap.seatData);
	seatList.sort();

	$(function(){
		$("#searchseats").autocomplete({
			source: seatList,
			select: function(event, ui){
				searchSeats(ui.item.label);
			}
		});
	});


	//show and hide various divs on load
	uiAttr.pageLoadDiv();

	// firefox css nonsense
	if(navigator.userAgent.toLowerCase().indexOf('firefox') > -1){
		$("div").css("font-size", "98%");
		$("select").css("font-size", "98%")
		// check this TO DO
	}

};

// various page interaction functions
function searchSeats(value){
	mapAttr.clicked(currentMap.seatData[value].mapSelect);
};


function pageSetting(name, mapurl, dataurl, previous, election, predict){

	this.name = name;
	this.mapurl = mapurl;
	this.dataurl = dataurl;
	this.previous = previous;
	this.election = election;
	this.predict = predict;

	this.seatData = {};
	this.polygons = {};
}

var currentMap;

var dataurls =  {
	// maps
	map650 : "650seat_no_ni.json",


	//parliaments
	brexit : "houseofcommons/brexit.json"

}

var brexit = new pageSetting("brexit", dataurls.map650, dataurls.brexit, dataurls.brexit, false, false);

function initialization(){

	var setting = brexit;

	uiAttr.changeNavBar(setting.name);

	$(document).ready(function(){
		getData(setting);
	});
}
